"""知识库管理器 — SQLite + LanceDB 双存储

独立于记忆系统，使用独立的 SQLite 数据库 (data/knowledge/knowledge.db)
和独立的 LanceDB 集合 (knowledge_base)。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
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
_KB_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".md", ".txt", ".markdown"}


class KnowledgeBaseManager:
    """知识库管理器。

    Usage:
        kb = KnowledgeBaseManager(workspace_root)
        doc_id = await kb.upload_document(file_path)
        results = await kb.search("查询内容", top_k=5)
    """

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

        self._init_sqlite()
        self._init_lancedb()
        self._scan_task = asyncio.create_task(self._scan_on_startup())

    def _init_sqlite(self) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
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
                    error_msg TEXT
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
            conn.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT OR IGNORE INTO _meta (key, value) VALUES ('schema_version', '1')")
            conn.commit()

    def _init_lancedb(self) -> None:
        import lancedb

        lance_path = str(self._workspace_root / "data" / "lancedb")
        self._lance_db = lancedb.connect(lance_path)
        self._ensure_lance_table()
        if self._lance_table is None and self.is_ready():
            asyncio.create_task(self._proactive_create_table())
        logger.info(
            "[KB] LanceDB initialized at %s, table=%s", lance_path, "knowledge_base"
        )

    def _ensure_lance_table(self) -> None:
        if "knowledge_base" not in self._lance_db.table_names():
            self._lance_table = None
        else:
            self._lance_table = self._lance_db.open_table("knowledge_base")
            if self._lance_table.count_rows() > 0:
                logger.info("[KB] Opened existing knowledge_base table")

    def _create_lance_table(self, dim: int) -> None:
        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dim)),
            pa.field("document_id", pa.string()),
        ])
        self._lance_table = self._lance_db.create_table(
            "knowledge_base", schema=schema, mode="overwrite"
        )
        self._embedding_dim = dim
        logger.info("[KB] Created knowledge_base table with dim=%d", dim)

    async def _proactive_create_table(self) -> None:
        """启动时主动创建 LanceDB 表，避免死锁：没表→不能上传→不能建表。"""
        try:
            dim = await self._get_embedding_dim()
            await asyncio.to_thread(self._create_lance_table, dim)
        except Exception as e:
            logger.warning("[KB] Proactive table creation failed: %s", e)

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

    async def upload_document(self, file_path: str | Path) -> str:
        """上传并处理文档，返回 doc_id。

        Args:
            file_path: 文件路径（可以是临时上传路径）

        Returns:
            文档 ID (UUID)

        Raises:
            ValueError: 文件类型不支持
            FileNotFoundError: 文件不存在
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        suffix = path.suffix.lower()
        if suffix not in _KB_ALLOWED_EXTENSIONS:
            raise ValueError(
                f"不支持的文件类型: {suffix}，支持: {sorted(_KB_ALLOWED_EXTENSIONS)}"
            )

        if path.stat().st_size > _KB_MAX_FILE_SIZE:
            raise ValueError(f"文件超过大小限制 ({_KB_MAX_FILE_SIZE // 1024 // 1024} MB)")

        doc_id = uuid.uuid4().hex

        with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "INSERT INTO knowledge_documents(id, name, file_type, upload_time, status) "
                "VALUES(?, ?, ?, ?, 'processing')",
                (doc_id, path.name, suffix.lstrip("."), time.time()),
            )
            conn.commit()

        asyncio.create_task(self._process_document(doc_id, path))

        return doc_id

    async def _process_document(self, doc_id: str, file_path: Path) -> None:
        """后台处理文档：提取文本 → 分块 → 向量化 → 存储。"""
        try:
            text = await asyncio.to_thread(extract_text, file_path)
            if not text or not text.strip():
                raise RuntimeError("文档内容为空")

            chunker = TextChunker(strategy="paragraph", max_chunk_size=1000)
            chunks = chunker.chunk(text)

            if not chunks:
                raise RuntimeError("未能从文档中提取任何内容块")

            embedder = await self._get_embedder()
            chunk_texts = [c.content for c in chunks]
            vectors = await embedder.embed(chunk_texts)

            if self._lance_table is None:
                dim = len(vectors[0]) if vectors else 0
                await asyncio.to_thread(self._create_lance_table, dim)

            lance_rows = [
                {"id": chunks[i].content[:50] + "_" + uuid.uuid4().hex[:8],
                 "vector": vectors[i],
                 "document_id": doc_id}
                for i in range(len(chunks))
            ]

            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
                for i, c in enumerate(chunks):
                    chunk_id = uuid.uuid4().hex
                    conn.execute(
                        "INSERT INTO knowledge_chunks(id, document_id, chunk_index, content, token_count) "
                        "VALUES(?, ?, ?, ?, ?)",
                        (chunk_id, doc_id, i, c.content, c.token_estimate),
                    )
                    lance_rows[i]["id"] = chunk_id

                conn.execute(
                    "UPDATE knowledge_documents SET total_chunks=? WHERE id=?",
                    (len(chunks), doc_id),
                )
                conn.commit()

            await asyncio.to_thread(self._lance_table.add, lance_rows)

            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    "UPDATE knowledge_documents SET status='ready' WHERE id=?",
                    (doc_id,),
                )
                conn.commit()

            logger.info(
                "[KB] Document %s processed: %d chunks",
                doc_id,
                len(chunks),
            )

        except Exception as e:
            logger.error("[KB] Failed to process document %s: %s", doc_id, e)
            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    "UPDATE knowledge_documents SET status='failed', error_msg=? WHERE id=?",
                    (str(e)[:500], doc_id),
                )
                conn.commit()

    async def list_documents(
        self, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """分页列出文档。"""
        def _query():
            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT id, name, file_type, upload_time, total_chunks, status, error_msg "
                    "FROM knowledge_documents ORDER BY upload_time DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_documents"
                ).fetchone()[0]
                return rows, total

        rows, total = await asyncio.to_thread(_query)
        documents = [dict(r) for r in rows]
        return {"documents": documents, "total": total}

    async def delete_document(self, doc_id: str) -> bool:
        """删除文档及其所有分块（SQLite + LanceDB）。"""
        def _delete():
            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
                cursor = conn.execute(
                    "SELECT id FROM knowledge_chunks WHERE document_id=?", (doc_id,)
                )
                chunk_ids = [row[0] for row in cursor.fetchall()]
                conn.execute(
                    "DELETE FROM knowledge_chunks WHERE document_id=?", (doc_id,)
                )
                conn.execute(
                    "DELETE FROM knowledge_documents WHERE id=?", (doc_id,)
                )
                conn.commit()
                return chunk_ids

        chunk_ids = await asyncio.to_thread(_delete)
        if not chunk_ids and not self._document_exists(doc_id):
            return False

        if chunk_ids and self._lance_table is not None:
            try:
                await asyncio.to_thread(
                    self._lance_table.delete,
                    f"document_id = '{doc_id}'",
                )

            except Exception as e:
                logger.warning("[KB] Failed to delete LanceDB vectors for %s: %s", doc_id, e)

        return True

    def _document_exists(self, doc_id: str) -> bool:
        with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM knowledge_documents WHERE id=?", (doc_id,)
            ).fetchone()
            return row is not None

    async def search(
        self,
        query: str,
        top_k: int = 5,
        doc_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """在知识库中搜索相关内容。

        Args:
            query: 搜索查询
            top_k: 返回结果数
            doc_filter: 可选，按文档 ID 过滤

        Returns:
            [{chunk_id, document_id, document_name, content, score}, ...]
        """
        if self._lance_table is None:
            return []

        try:
            embedder = await self._get_embedder()
            query_vec = await embedder.embed_query(query)
        except Exception as e:
            logger.warning("[KB] Embedding model not available, search skipped: %s", e)
            return []

        def _search():
            if doc_filter:
                results = (
                    self._lance_table.search(query_vec)
                    .metric("cosine")
                    .where(f"document_id = '{doc_filter}'")
                    .limit(top_k)
                    .to_list()
                )
            else:
                results = (
                    self._lance_table.search(query_vec)
                    .metric("cosine")
                    .limit(top_k)
                    .to_list()
                )

            distance_multiplier = 2.0
            cosine_scores: list[tuple[str, float, str]] = []
            for r in results:
                dist = r.get("_distance", 1.0)
                score = 1.0 - dist / distance_multiplier
                chunk_id = r.get("id", "")
                doc_id = r.get("document_id", "")
                cosine_scores.append((chunk_id, max(0.0, min(1.0, score)), doc_id))

            chunk_data = {}
            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
                for chunk_id, score, doc_id in cosine_scores:
                    row = conn.execute(
                        """SELECT kc.content, kc.chunk_index, kd.name as document_name
                               FROM knowledge_chunks kc
                               JOIN knowledge_documents kd ON kc.document_id = kd.id
                               WHERE kc.id = ?""",
                        (chunk_id,),
                    ).fetchone()
                    if row:
                        chunk_data[chunk_id] = {
                            "chunk_id": chunk_id,
                            "document_id": doc_id,
                            "document_name": row[2],
                            "content": row[0],
                            "score": score,
                        }

            return [chunk_data[cid] for cid, _, _ in cosine_scores if cid in chunk_data]

        results = await asyncio.to_thread(_search)
        return results

    async def get_document_status(self, doc_id: str) -> dict[str, Any] | None:
        """获取文档处理状态。"""
        def _query():
            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
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
            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
                row = conn.execute(
                    "SELECT content FROM knowledge_chunks WHERE id=?",
                    (chunk_id,),
                ).fetchone()
                return row[0] if row else None

        return await asyncio.to_thread(_query)

    async def get_document_by_id(self, doc_id: str) -> dict[str, Any] | None:
        """获取单个文档的详细信息。"""
        def _query():
            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
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
            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
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
        """修复文档：从 SQLite 分块重建 LanceDB 向量索引。"""
        def _read_chunks():
            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
                rows = conn.execute(
                    "SELECT id, content FROM knowledge_chunks WHERE document_id=? ORDER BY chunk_index",
                    (doc_id,),
                ).fetchall()
                return [(r[0], r[1]) for r in rows]

        chunks = await asyncio.to_thread(_read_chunks)
        if not chunks:
            return {"repaired": False, "reason": "文档无分块记录"}

        embedder = await self._get_embedder()
        texts = [c[1] for c in chunks]
        vectors = await embedder.embed(texts)

        if self._lance_table is None:
            dim = len(vectors[0]) if vectors else 0
            await asyncio.to_thread(self._create_lance_table, dim)

        try:
            await asyncio.to_thread(
                self._lance_table.delete, f"document_id = '{doc_id}'"
            )
        except Exception:
            pass

        lance_rows = [
            {"id": chunks[i][0], "vector": vectors[i], "document_id": doc_id}
            for i in range(len(chunks))
        ]
        await asyncio.to_thread(self._lance_table.add, lance_rows)

        with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "UPDATE knowledge_documents SET status='ready', error_msg=NULL WHERE id=?",
                (doc_id,),
            )
            conn.commit()

        logger.info("[KB] Repaired document %s: %d chunks", doc_id, len(chunks))
        return {"repaired": True, "chunks": len(chunks)}

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
        except Exception:
            lance_ids = set()

        def _get_valid_ids():
            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
                rows = conn.execute("SELECT id FROM knowledge_documents").fetchall()
                return {r[0] for r in rows}

        valid_ids = await asyncio.to_thread(_get_valid_ids)
        orphan_ids = lance_ids - valid_ids

        cleaned = 0
        for oid in orphan_ids:
            try:
                await asyncio.to_thread(
                    self._lance_table.delete, f"document_id = '{oid}'"
                )
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
            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
                rows = conn.execute(
                    "SELECT id, name, total_chunks, status "
                    "FROM knowledge_documents WHERE status='ready'"
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
                inconsistent.append({
                    "doc_id": doc_id,
                    "name": name,
                    "status": status,
                    "sqlite_chunks": sqlite_count,
                    "lancedb_vectors": lance_count,
                })

        return inconsistent

    async def verify_document(self, doc_id: str) -> dict[str, Any] | None:
        """返回文档在 SQLite 和 LanceDB 中的记录数对比。"""
        def _get_sqlite():
            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
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
                return {"id": row[0], "name": row[1], "status": row[2], "total_chunks": row[3], "actual_chunks": chunk_count}

        sqlite_info = await asyncio.to_thread(_get_sqlite)
        if sqlite_info is None:
            return None

        lance_count = 0
        if self._lance_table is not None:
            try:
                lance_count = await asyncio.to_thread(
                    lambda: self._lance_table.count_rows(f"document_id = '{doc_id}'")
                )
            except Exception:
                lance_count = 0

        return {
            **sqlite_info,
            "lancedb_vectors": lance_count,
            "consistent": lance_count == sqlite_info["actual_chunks"],
        }

    async def get_graph_data(
        self,
        doc_id: str | None = None,
        include_semantic: bool = False,
        similarity_threshold: float = 0.75,
        max_nodes: int = 2000,
    ) -> dict[str, Any]:
        """获取图谱数据：节点（chunk）+ 边（顺序 + 可选语义）。

        Args:
            doc_id: 可选，过滤特定文档
            include_semantic: 是否包含语义相似边
            similarity_threshold: 语义边相似度阈值 (0.5~0.95)
            max_nodes: 最大节点数

        Returns:
            {"nodes": [...], "links": [...], "meta": {...}}
        """
        import random as _random

        threshold = max(0.5, min(0.95, similarity_threshold))

        def _query_chunks():
            with self._write_lock, sqlite3.connect(str(self._db_path)) as conn:
                if doc_id:
                    rows = conn.execute(
                        """SELECT kc.id, kc.content, kc.chunk_index, kc.document_id, kd.name as doc_name
                           FROM knowledge_chunks kc
                           JOIN knowledge_documents kd ON kc.document_id = kd.id
                           WHERE kc.document_id = ?
                           ORDER BY kc.chunk_index""",
                        (doc_id,),
                    ).fetchall()
                    total = len(rows)
                    rows = rows[:max_nodes]
                else:
                    total = conn.execute(
                        """SELECT COUNT(*) FROM knowledge_chunks kc
                           JOIN knowledge_documents kd ON kc.document_id = kd.id
                           WHERE kd.status = 'ready'"""
                    ).fetchone()[0]
                    rows = conn.execute(
                        """SELECT kc.id, kc.content, kc.chunk_index, kc.document_id, kd.name as doc_name
                           FROM knowledge_chunks kc
                           JOIN knowledge_documents kd ON kc.document_id = kd.id
                           WHERE kd.status = 'ready'
                           ORDER BY kc.document_id, kc.chunk_index
                           LIMIT ?""",
                        (max_nodes,),
                    ).fetchall()
                return [(r[0], r[1] or "", r[2], r[3], r[4]) for r in rows], total

        chunks, total_candidates = await asyncio.to_thread(_query_chunks)
        if not chunks:
            return {"nodes": [], "links": [], "meta": {"total_nodes": 0, "total_edges": 0, "truncated": False, "total_candidates": 0}}

        nodes: list[dict] = []
        nodes_by_id: dict[str, dict] = {}

        for cid, content, idx, did, dname in chunks:
            name = content[:80].replace("\n", " ").strip()
            if len(content) > 80:
                name += "..."
            node = {
                "id": cid,
                "name": name,
                "doc_name": dname,
                "group": did,
                "chunk_index": idx,
                "content": content,
            }
            nodes.append(node)
            nodes_by_id[cid] = node

        links: list[dict] = []
        seen_pairs: set[tuple[str, str]] = set()

        chunks_by_doc: dict[str, list[dict]] = {}
        for node in nodes:
            chunks_by_doc.setdefault(node["group"], []).append(node)

        for _did, doc_chunks in chunks_by_doc.items():
            sorted_chunks = sorted(doc_chunks, key=lambda n: n["chunk_index"])
            for i in range(len(sorted_chunks) - 1):
                s = sorted_chunks[i]["id"]
                t = sorted_chunks[i + 1]["id"]
                key = (s, t) if s < t else (t, s)
                if key not in seen_pairs:
                    seen_pairs.add(key)
                    links.append({"source": s, "target": t, "value": 1})

        semantic_sampled = 0
        semantic_incomplete = False
        if include_semantic and self._lance_table is not None:
            try:
                async def _compute_semantic():
                    nonlocal semantic_sampled
                    embedder = await self._get_embedder()
                    if doc_id:
                        sample_nodes = nodes[:min(len(nodes), 200)]
                    else:
                        sample_indices = _random.sample(
                            range(len(nodes)), min(200, len(nodes))
                        )
                        sample_nodes = [nodes[i] for i in sorted(sample_indices)]

                    semantic_sampled = len(sample_nodes)
                    for node in sample_nodes:
                        try:
                            vec = await embedder.embed_query(node["content"][:300])
                            node_group = node["group"]
                            lance_results = await asyncio.to_thread(
                                lambda v=vec, g=node_group: self._lance_table.search(v)
                                .metric("cosine")
                                .where(f"document_id != '{g}'")
                                .limit(3)
                                .to_list(),
                            )
                            for r in lance_results:
                                score = 1.0 - r.get("_distance", 1.0) / 2.0
                                if score >= threshold:
                                    tid = r.get("id", "")
                                    if tid not in nodes_by_id:
                                        continue
                                    key = (node["id"], tid) if node["id"] < tid else (tid, node["id"])
                                    if key not in seen_pairs:
                                        seen_pairs.add(key)
                                        links.append({
                                            "source": node["id"],
                                            "target": tid,
                                            "value": round(score, 3),
                                        })
                        except Exception:
                            pass

                await asyncio.wait_for(_compute_semantic(), timeout=30)
            except TimeoutError:
                logger.warning("[KB] Semantic edges computation timed out after 30s")
                semantic_incomplete = True
            except Exception as e:
                logger.warning("[KB] Semantic edges computation failed: %s", e)

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
                "semantic_sampled_count": semantic_sampled,
                "semantic_incomplete": semantic_incomplete,
            },
        }
