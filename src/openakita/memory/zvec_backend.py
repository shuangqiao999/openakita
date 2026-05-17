"""
Zvec 向量后端 — 默认的记忆向量存储后端

实现 SearchBackend Protocol 接口。
内部使用 get_embedding_model() 将文本转换为向量后存储/搜索。
不可用时自动降级到 FTS5。

Zvec 是阿里开源的高性能嵌入式向量数据库，内存可控，适合本地桌面应用。
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from .json_utils import coerce_text

logger = logging.getLogger(__name__)


class ZvecNotAvailableError(Exception):
    """Zvec 未安装或初始化失败"""


class ZvecBackend:
    """Zvec 向量存储后端。

    实现 SearchBackend Protocol — search/add/batch_add 均为文本接口。
    内部自动调用嵌入模型将文本转为向量。
    """

    def __init__(
        self,
        persist_dir: str = "data/zvec",
        embedding_dim: int = 1536,
        metric: str = "cosine",
    ):
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._embedding_dim = embedding_dim
        self._metric = metric
        self._enabled = False
        self._collection: object | None = None
        self._zvec = None

        try:
            import zvec

            self._zvec = zvec
            self._collection = zvec.Collection(
                name="openakita_memories",
                dim=embedding_dim,
                path=str(self._persist_dir),
                metric=metric,
            )
            self._enabled = True
            logger.info(
                f"[ZvecBackend] Initialized: dim={embedding_dim}, "
                f"metric={metric}, path={self._persist_dir}"
            )
        except ImportError:
            logger.warning(
                "[ZvecBackend] zvec not installed — install with: pip install zvec"
            )
        except Exception as e:
            logger.warning(f"[ZvecBackend] Init failed: {e}")

    @property
    def available(self) -> bool:
        return self._enabled and self._collection is not None

    @property
    def backend_type(self) -> str:
        return "zvec"

    # ── 嵌入模型懒加载 ──

    def _get_embedder(self):
        try:
            from openakita.llm.embeddings import get_embedding_model

            return get_embedding_model()
        except Exception:
            return None

    # ── SearchBackend Protocol 实现 (文本接口) ──

    def search(
        self,
        query: str,
        limit: int = 10,
        filter_type: str | None = None,
        scope: str | None = None,
        scope_owner: str | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[tuple[str, float]]:
        """文本语义搜索 — 内部嵌入查询文本后做向量检索"""
        if not self.available:
            return []
        embedder = self._get_embedder()
        if embedder is None:
            logger.warning("[ZvecBackend] No embedding model available, skipping vector search")
            return []
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                query_vec = loop.run_until_complete(embedder.embed_query(query))
            else:
                query_vec = asyncio.run(embedder.embed_query(query))
        except RuntimeError:
            query_vec = asyncio.run(embedder.embed_query(query))
        return self._search_vector(query_vec, limit)

    def _search_vector(
        self, query_vec: list[float], top_k: int = 10
    ) -> list[tuple[str, float]]:
        if not self.available or not query_vec:
            return []
        try:
            results = self._collection.query(
                query_embeddings=[query_vec],
                n_results=min(top_k, 50),
            )
            if not results or not results.get("ids"):
                return []
            ids_list = results["ids"]
            distances = results.get("distances")
            if not ids_list or not ids_list[0]:
                return []
            ids = ids_list[0]
            dists = distances[0] if distances else [1.0] * len(ids)

            scored: list[tuple[str, float]] = []
            for mid, dist in zip(ids, dists, strict=False):
                if self._metric == "cosine":
                    score = float(1.0 - min(dist, 2.0))
                else:
                    score = float(1.0 / (1.0 + max(dist, 0.0)))
                score = max(0.0, min(1.0, score))
                if math.isfinite(score):
                    scored.append((coerce_text(mid), score))
            return scored
        except Exception as e:
            logger.warning(f"[ZvecBackend] _search_vector failed: {e}")
            return []

    def add(self, memory_id: str, content: str, metadata: dict | None = None) -> bool:
        """添加记忆 — 内部嵌入内容文本后存储向量"""
        if not self.available:
            return False
        embedder = self._get_embedder()
        if embedder is None:
            return False
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                vec = loop.run_until_complete(embedder.embed_query(content))
            else:
                vec = asyncio.run(embedder.embed_query(content))
        except RuntimeError:
            vec = asyncio.run(embedder.embed_query(content))

        if not vec:
            return False
        try:
            self._collection.add(
                ids=[memory_id],
                embeddings=[vec],
                metadatas=[metadata or {}],
            )
            return True
        except Exception as e:
            logger.warning(f"[ZvecBackend] add failed for {memory_id[:8]}: {e}")
            return False

    def delete(self, memory_id: str) -> bool:
        if not self.available:
            return False
        try:
            self._collection.delete(ids=[memory_id])
            return True
        except Exception as e:
            logger.warning(f"[ZvecBackend] delete failed for {memory_id[:8]}: {e}")
            return False

    def batch_add(self, items: list[dict]) -> int:
        """批量添加。items: [{"id": str, "content": str, "metadata": dict}]"""
        if not self.available or not items:
            return 0
        embedder = self._get_embedder()
        if embedder is None:
            return 0
        import asyncio

        contents = [it.get("content", "") for it in items]
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                vecs = loop.run_until_complete(embedder.embed(contents))
            else:
                vecs = asyncio.run(embedder.embed(contents))
        except RuntimeError:
            vecs = asyncio.run(embedder.embed(contents))

        if not vecs:
            return 0
        try:
            ids = [it["id"] for it in items]
            metadatas = [it.get("metadata", {}) for it in items]
            self._collection.add(ids=ids, embeddings=vecs, metadatas=metadatas)
            return len(items)
        except Exception as e:
            logger.warning(f"[ZvecBackend] batch_add failed: {e}")
            return 0

    def count(self) -> int:
        if not self.available:
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0

    def clear(self) -> bool:
        if not self.available:
            return False
        try:
            self._collection.drop()
            self._collection = self._zvec.Collection(
                name="openakita_memories",
                dim=self._embedding_dim,
                path=str(self._persist_dir),
                metric=self._metric,
            )
            return True
        except Exception as e:
            logger.warning(f"[ZvecBackend] clear failed: {e}")
            return False
