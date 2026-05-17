"""
Zvec 向量后端 — 默认的记忆向量存储后端

实现 SearchBackend Protocol 接口。
内部使用 get_embedding_model() 将文本转换为向量后存储/搜索。
不可用时自动降级到 FTS5。

zvec 实际 API (v0.4.0):
- 创建: zvec.create_and_open(path, zvec.CollectionSchema(name, vectors=[VectorSchema(...)]))
- 插入: collection.insert([zvec.Doc(id=, vectors={"embedding": [...]})])
- 查询: collection.query(queries=Query(field_name, vector=[...]), topk=N)
- 删除: collection.delete(ids=[...])
- 销毁: collection.destroy()
- 统计: collection.stats (CollectionStats with row_count)
"""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path

from .json_utils import coerce_text

logger = logging.getLogger(__name__)


def _run_embedding_sync(embedder, method_name: str, *args):
    """安全地在同步上下文中调用异步嵌入。"""
    import asyncio

    method = getattr(embedder, method_name)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return asyncio.run(method(*args))

    if loop.is_running():
        future = asyncio.run_coroutine_threadsafe(method(*args), loop)
        return future.result(timeout=120)
    else:
        return asyncio.run(method(*args))


class ZvecBackend:
    """Zvec 向量存储后端。

    实现 SearchBackend Protocol — search/add/batch_add 均为文本接口。
    内部自动调用嵌入模型将文本转为向量。
    使用 zvec 真实 API: CollectionSchema + Doc + VectorQuery。

    线程安全: 所有对 _collection 的访问由 _lock 保护。

    延迟创建: _collection 在首次 add() 时根据嵌入模型维度自动创建。
    """

    _EMBEDDING_FIELD = "embedding"

    def __init__(
        self,
        persist_dir: str = "data/zvec",
        embedding_dim: int = 0,
        metric: str = "cosine",
    ):
        self._persist_dir = Path(persist_dir)
        self._metric = metric
        self._embedding_dim = embedding_dim
        self._enabled = False
        self._collection: object | None = None
        self._zvec = None
        self._lock = threading.Lock()
        self._cached_embedder: object | None = None

        try:
            import zvec as _zvec_mod

            self._zvec = _zvec_mod
        except ImportError:
            logger.warning(
                "[ZvecBackend] zvec not installed — install with: pip install zvec"
            )
            return
        except Exception as e:
            logger.warning(f"[ZvecBackend] zvec import failed: {e}")
            return

        # 尝试打开已有 collection
        self._init_or_open(embedding_dim)

    def _init_or_open(self, embedding_dim: int) -> None:
        """打开已有 collection 或标记为待创建"""
        try:
            coll_path = str(self._persist_dir / "@openakita_memories")
            collection_exists = self._zvec and Path(coll_path).is_dir()
            if collection_exists:
                self._open_collection(coll_path)
            elif embedding_dim > 0:
                self._ensure_collection(coll_path, embedding_dim)
            else:
                logger.info(
                    "[ZvecBackend] zvec available, collection not created yet "
                    "(will auto-create on first insert with detected embedding dim)"
                )
                self._enabled = False
        except Exception as e:
            logger.warning(f"[ZvecBackend] Init/open failed: {e}")
            self._enabled = False

    def _open_collection(self, coll_path: str) -> None:
        """打开已有 collection，处理残留 LOCK 文件和 API 兼容性"""
        lock_path = Path(coll_path) / "LOCK"
        lock_stale = False
        try:
            self._collection = self._zvec.open(path=coll_path)
        except Exception as e:
            err_msg = str(e).lower()
            if "lock" in err_msg and lock_path.exists():
                lock_stale = True
                logger.warning(
                    f"[ZvecBackend] Stale LOCK file detected at {lock_path}, removing and retrying"
                )
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                self._collection = self._zvec.open(path=coll_path)
            else:
                raise

        self._enabled = True
        self._embedding_dim = self._read_schema_dim(
            self._collection, coll_path, lock_stale
        )

    @staticmethod
    def _read_schema_dim(collection, coll_path: str, lock_stale: bool) -> int:
        """从 collection schema 读取向量维度，兼容多个 zvec API 版本"""
        schema = getattr(collection, "schema", None)
        if schema is None:
            logger.warning(f"[ZvecBackend] Collection has no schema attr at {coll_path}")
            return 0
        # 兼容: vector_schemas (新版) / vectors (旧版) / vector_schema (单数别名)
        for attr in ("vector_schemas", "vectors", "vector_schema"):
            vs_list = getattr(schema, attr, None)
            if vs_list is None:
                continue
            if not isinstance(vs_list, (list, tuple)):
                vs_list = [vs_list]
            for vs in vs_list:
                name = getattr(vs, "name", "")
                dim = getattr(vs, "dimension", getattr(vs, "dim", 0))
                embedding_field = ZvecBackend._EMBEDDING_FIELD
                if name == embedding_field and dim > 0:
                    logger.info(
                        f"[ZvecBackend] Opened existing collection: dim={dim}, "
                        f"path={coll_path}" + (" (stale LOCK cleaned)" if lock_stale else "")
                    )
                    return dim
        logger.warning(
            f"[ZvecBackend] Could not determine embedding dim from schema at {coll_path}"
        )
        return 0

    def _ensure_collection(self, coll_path: str, embedding_dim: int) -> None:
        """创建 collection (幂等)"""
        if self._collection is not None:
            return
        if embedding_dim <= 0:
            return
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        schema = self._zvec.CollectionSchema(
            name="openakita_memories",
            vectors=[
                self._zvec.VectorSchema(
                    self._EMBEDDING_FIELD,
                    self._zvec.DataType.VECTOR_FP32,
                    embedding_dim,
                )
            ],
        )
        # 设置 metric type
        metric_map = {
            "cosine": self._zvec.MetricType.COSINE,
            "ip": self._zvec.MetricType.IP,
            "l2": self._zvec.MetricType.L2,
        }
        mt = metric_map.get(self._metric.lower(), self._zvec.MetricType.COSINE)
        schema.metric_type = mt

        self._collection = self._zvec.create_and_open(path=coll_path, schema=schema)
        self._embedding_dim = embedding_dim
        self._enabled = True
        logger.info(
            f"[ZvecBackend] Created collection: dim={embedding_dim}, "
            f"metric={self._metric}, path={coll_path}"
        )

    @property
    def available(self) -> bool:
        return self._enabled and self._collection is not None

    @property
    def backend_type(self) -> str:
        return "zvec"

    def _get_embedder(self):
        if self._cached_embedder is not None:
            return self._cached_embedder
        try:
            from openakita.llm.embeddings import get_embedding_model

            model = get_embedding_model()
            self._cached_embedder = model
            return model
        except Exception:
            return None

    def _coll_path(self) -> str:
        return str(self._persist_dir / "@openakita_memories")

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
        if not self.available:
            return []
        embedder = self._get_embedder()
        if embedder is None:
            logger.warning("[ZvecBackend] No embedding model available, skipping vector search")
            return []
        try:
            query_vec = _run_embedding_sync(embedder, "embed_query", query)
        except Exception as e:
            logger.warning(f"[ZvecBackend] Embedding for search failed: {e}")
            return []

        if not query_vec:
            return []

        try:
            zq = self._zvec.Query(field_name=self._EMBEDDING_FIELD, vector=query_vec)
            with self._lock:
                results = self._collection.query(
                    queries=zq,
                    topk=min(limit, 50),
                    include_vector=False,
                )
            if not results:
                return []

            scored: list[tuple[str, float]] = []
            for doc in results:
                doc_id = doc.id if hasattr(doc, "id") else ""
                if not doc_id:
                    continue
                raw_score = doc.score if hasattr(doc, "score") else 0.0
                if self._metric == "cosine":
                    score = float(raw_score)
                elif self._metric == "ip":
                    # Inner product score normalize to [0, 1]
                    score = float(max(0.0, min(1.0, raw_score)))
                else:
                    # L2 (Euclidean): lower distance = more similar, invert to [0, 1]
                    score = 1.0 / (1.0 + float(raw_score))
                score = max(0.0, min(1.0, score))
                if math.isfinite(score):
                    scored.append((coerce_text(doc_id), score))
            return scored
        except Exception as e:
            logger.warning(f"[ZvecBackend] search failed: {e}")
            return []

    def add(self, memory_id: str, content: str, metadata: dict | None = None) -> bool:
        if not self._zvec:
            return False

        embedder = self._get_embedder()
        if embedder is None:
            return False

        vec_dim = getattr(embedder, "dimension", 0) or 0
        if vec_dim <= 0:
            return False

        try:
            vec = _run_embedding_sync(embedder, "embed_query", content)
        except Exception as e:
            logger.warning(f"[ZvecBackend] Embedding for add failed: {e}")
            return False

        if not vec:
            return False

        with self._lock:
            if not self._collection and vec_dim > 0:
                try:
                    self._ensure_collection(self._coll_path(), vec_dim)
                except Exception as e:
                    logger.warning(f"[ZvecBackend] Auto-create collection failed: {e}")
                    return False

            if not self._collection:
                return False

            try:
                doc = self._zvec.Doc(id=memory_id, vectors={self._EMBEDDING_FIELD: vec})
                if metadata:
                    for k, v in metadata.items():
                        if isinstance(v, (str, int, float, bool)):
                            setattr(doc, k, v)
                self._collection.insert([doc])
                self._collection.flush()
                return True
            except Exception as e:
                logger.warning(f"[ZvecBackend] add failed for {memory_id[:8]}: {e}")
                return False

    def delete(self, memory_id: str) -> bool:
        if not self.available:
            return False
        try:
            with self._lock:
                self._collection.delete(ids=[memory_id])
                self._collection.flush()
            return True
        except Exception as e:
            logger.warning(f"[ZvecBackend] delete failed for {memory_id[:8]}: {e}")
            return False

    def batch_add(self, items: list[dict]) -> int:
        if not self._zvec:
            return 0

        embedder = self._get_embedder()
        if embedder is None:
            return 0

        contents = [it.get("content", "") for it in items]
        try:
            vecs = _run_embedding_sync(embedder, "embed", contents)
        except Exception as e:
            logger.warning(f"[ZvecBackend] Batch embedding failed: {e}")
            return 0

        if not vecs or len(vecs) != len(items) or any(not v for v in vecs):
            return 0

        vec_dim = getattr(embedder, "dimension", 0) or 0

        with self._lock:
            if not self._collection and vec_dim > 0:
                try:
                    self._ensure_collection(self._coll_path(), vec_dim)
                except Exception as e:
                    logger.warning(f"[ZvecBackend] Auto-create collection failed: {e}")
                    return 0

            if not self._collection:
                return 0

            try:
                docs = []
                for i, it in enumerate(items):
                    doc_id = it.get("id", "")
                    if not doc_id:
                        continue
                    doc = self._zvec.Doc(
                        id=doc_id,
                        vectors={self._EMBEDDING_FIELD: vecs[i]},
                    )
                    metadata = it.get("metadata")
                    if metadata and isinstance(metadata, dict):
                        for k, v in metadata.items():
                            if isinstance(v, (str, int, float, bool)):
                                setattr(doc, k, v)
                    docs.append(doc)
                if docs:
                    self._collection.insert(docs)
                    self._collection.flush()
                return len(docs)
            except Exception as e:
                logger.warning(f"[ZvecBackend] batch_add failed: {e}")
                return 0

    def count(self) -> int:
        if not self.available:
            return 0
        try:
            with self._lock:
                stats = self._collection.stats
                return getattr(stats, "row_count", 0) if stats else 0
        except Exception as e:
            logger.warning(f"[ZvecBackend] count failed: {e}")
            return 0

    def clear(self) -> bool:
        if not self._zvec or not self._collection:
            return False
        try:
            with self._lock:
                self._collection.destroy()
                self._collection = None
                self._enabled = False
            return True
        except Exception as e:
            logger.warning(f"[ZvecBackend] clear failed: {e}")
            return False
