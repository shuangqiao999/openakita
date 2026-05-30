"""
LanceDB 向量后端 — 默认的记忆向量存储后端

实现 SearchBackend Protocol 接口。
内部使用 get_embedding_model() 将文本转换为向量后存储/搜索。
不可用时自动降级到 FTS5。

LanceDB Python SDK 核心 API:
- 连接: lancedb.connect(path)
- 创建: db.create_table(name, schema=schema)
- 打开: db.open_table(name)
- 插入: table.add(rows)   rows 为字典列表
- 查询: table.search(vector).metric("cosine").limit(N).to_list()
- 删除: table.delete("id = '...'")
- 统计: table.count_rows()
"""

from __future__ import annotations

import gc
import json
import logging
import math
import threading
from collections import OrderedDict
from pathlib import Path

from .json_utils import coerce_text

logger = logging.getLogger(__name__)

_EMBEDDING_TIMEOUT_SEC = 15


def _run_embedding_sync(embedder, method_name: str, *args):
    """安全地在同步上下文中调用异步嵌入。

    超时时间由 ``_EMBEDDING_TIMEOUT_SEC`` 控制（默认 15s）。
    超时时返回 None，由调用方处理降级。

    处理三种线程上下文：
    1. 无事件循环（纯同步脚本）→ asyncio.run() 创建临时循环
    2. 当前线程有运行中的事件循环（如 FastAPI handler）→
       此时 run_coroutine_threadsafe + future.result() 会死锁，
       改用 ThreadPoolExecutor 在线程池中创建独立事件循环执行
    3. 其他线程存在运行中的主事件循环（如 threading.Thread）→
       run_coroutine_threadsafe 提交到主循环，正确
    """
    import asyncio
    import concurrent.futures

    method = getattr(embedder, method_name)

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(asyncio.run, method(*args))
            try:
                return future.result(timeout=_EMBEDDING_TIMEOUT_SEC)
            except concurrent.futures.TimeoutError:
                logger.error(
                    "[LanceDBBackend] Embedding call timed out after %ds",
                    _EMBEDDING_TIMEOUT_SEC,
                )
                return None
            except Exception as e:
                logger.error("[LanceDBBackend] Embedding call failed: %s", e)
                return None
    except RuntimeError:
        pass

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return asyncio.run(method(*args))

    if loop.is_running():
        future = asyncio.run_coroutine_threadsafe(method(*args), loop)
        try:
            return future.result(timeout=_EMBEDDING_TIMEOUT_SEC)
        except TimeoutError:
            logger.error(
                "[LanceDBBackend] Embedding call timed out after %ds",
                _EMBEDDING_TIMEOUT_SEC,
            )
            return None
        except Exception as e:
            logger.error("[LanceDBBackend] Embedding call failed: %s", e)
            return None
    else:
        return asyncio.run(method(*args))


class LanceDBBackend:
    """LanceDB 向量存储后端。

    实现 SearchBackend Protocol — search/add/batch_add 均为文本接口。
    内部自动调用嵌入模型将文本转为向量。

    线程安全: 所有对 _table 的写访问由 _lock 保护。

    延迟创建: _table 在首次 add() 时根据嵌入模型维度自动创建。
    若维度变更（换用不同模型），自动删除旧表并重建。
    """

    _METRIC = "cosine"
    _MIN_ROWS_FOR_INDEX = 200
    _INDEX_TYPE = "IVF_PQ"
    _INDEX_FTS_FIELD = "content"
    _QUERY_EMBED_CACHE_SIZE = 256

    def __init__(
        self,
        persist_dir: str = "data/lancedb",
        embedding_dim: int = 0,
        on_rebuild: object = None,
    ):
        self._persist_dir = Path(persist_dir)
        self._embedding_dim = embedding_dim
        self._enabled = False
        self._db: object | None = None
        self._table: object | None = None
        self._lancedb = None
        self._lock = threading.Lock()
        self._cached_embedder: object | None = None
        self._embedder_pinged = False
        self._embedding_failures = 0
        self._embedding_healthy = True
        self._embedding_last_error: str | None = None
        self._on_rebuild = on_rebuild
        self._index_created = False
        self._creating_index = False
        self._fts_index_created = False
        self._creating_fts_index = False
        self._query_embed_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._episodes_table: object | None = None
        self._episodes_enabled = False

        try:
            import lancedb as _mod

            self._lancedb = _mod
        except ImportError:
            logger.warning(
                "[LanceDBBackend] lancedb not installed — "
                "install with: pip install lancedb"
            )
            return
        except Exception as e:
            logger.warning(f"[LanceDBBackend] lancedb import failed: {e}")
            return

        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._init_or_open(embedding_dim)

    # ── Init / Open ──

    def _table_name(self) -> str:
        return "openakita_memories"

    def _episodes_table_name(self) -> str:
        return "openakita_episodes"

    def _init_or_open(self, embedding_dim: int) -> None:
        try:
            db = self._lancedb.connect(str(self._persist_dir))
            self._db = db
            table_names = db.list_tables()
            if self._table_name() in table_names:
                self._table = db.open_table(self._table_name())
                self._embedding_dim = self._read_table_dim()
                self._enabled = True
                try:
                    total = self._table.count_rows()
                    logger.info(
                        "[LanceDBBackend] Opened existing table: dim=%d, rows=%d",
                        self._embedding_dim,
                        total,
                    )
                except Exception as e:
                    logger.warning(
                        "[LanceDBBackend] Table corrupted: %s. Dropping and will rebuild.", e
                    )
                    self._table = None
                    self._enabled = False
                    try:
                        db.drop_table(self._table_name())
                    except Exception:
                        pass
                    if embedding_dim > 0:
                        self._ensure_table(embedding_dim)
                else:
                    self._create_index_async()
                    self._create_fts_index_async()
            elif embedding_dim > 0:
                self._ensure_table(embedding_dim)
            else:
                logger.info(
                    "[LanceDBBackend] lancedb available, table not created yet "
                    "(will auto-create on first insert)"
                )
                self._enabled = False

            # Also open episodes table if it exists
            if self._episodes_table_name() in table_names:
                try:
                    self._episodes_table = db.open_table(self._episodes_table_name())
                    self._episodes_enabled = True
                    total = self._episodes_table.count_rows()
                    logger.info(
                        "[LanceDBBackend] Opened existing episodes table: rows=%d",
                        total,
                    )
                except Exception as e:
                    logger.warning(
                        "[LanceDBBackend] Episodes table corrupted: %s — dropping and will rebuild", e
                    )
                    self._episodes_table = None
                    self._episodes_enabled = False
                    try:
                        db.drop_table(self._episodes_table_name())
                    except Exception:
                        pass
                    if self._embedding_dim > 0:
                        self._ensure_episodes_table(self._embedding_dim)
        except Exception as e:
            logger.warning(f"[LanceDBBackend] Init/open failed: {e}")
            self._enabled = False

    def _read_table_dim(self) -> int:
        return self._read_table_dim_for(self._table)

    @staticmethod
    def _read_table_dim_for(table: object) -> int:
        """从 LanceDB table schema 读取向量维度"""
        if table is None:
            return 0
        try:
            schema = table.schema
            vec_field = schema.field("vector")
            if vec_field is not None:
                dim = getattr(vec_field.type, "list_size", 0)
                if dim > 0:
                    return dim
        except Exception:
            pass
        return 0

    # ── Vector Index ──

    def _should_create_index(self) -> bool:
        if self._creating_index or self._index_created:
            return False
        if self._table is None or not self._enabled:
            return False
        try:
            row_count = self._table.count_rows()
        except Exception:
            return False
        if row_count < self._MIN_ROWS_FOR_INDEX:
            logger.debug(
                "[LanceDBBackend] Rows %d below index threshold %d, skipping",
                row_count,
                self._MIN_ROWS_FOR_INDEX,
            )
            return False
        try:
            existing = list(self._table.list_indices())
            if existing:
                self._index_created = True
                logger.info(
                    "[LanceDBBackend] Index already exists (%d index(es)), skipping",
                    len(existing),
                )
                return False
        except Exception:
            return False
        return True

    def _create_index_sync(self) -> None:
        try:
            row_count = self._table.count_rows()
            num_partitions = max(2, min(256, int(row_count ** 0.5)))
            dim = self._embedding_dim
            # Prefer sub-vector size 32-64 for best recall/speed tradeoff.
            # Try larger divisors first (fewer sub_vectors = less compression, better recall).
            num_sub = 1
            for d in (64, 32, 16, 96, 48, 8, 128):
                if dim % d == 0:
                    num_sub = dim // d
                    break
            logger.info(
                "[LanceDBBackend] Index creation started "
                "(type=%s, rows=%d, dim=%d, partitions=%d, sub_vectors=%d)",
                self._INDEX_TYPE, row_count, dim, num_partitions, num_sub,
            )
            self._table.create_index(
                metric=self._METRIC,
                num_partitions=num_partitions,
                num_sub_vectors=num_sub,
                index_type=self._INDEX_TYPE,
                replace=True,
            )
            self._index_created = True
            logger.info("[LanceDBBackend] Index creation completed")
        except Exception as e:
            logger.warning("[LanceDBBackend] Index creation failed: %s", e)
        finally:
            self._creating_index = False

    def _create_index_async(self) -> None:
        if not self._should_create_index():
            return
        self._creating_index = True
        t = threading.Thread(target=self._create_index_sync, daemon=True)
        t.start()
        logger.debug("[LanceDBBackend] Index creation dispatched to background thread")

    # ── Full-Text Search Index ──

    def _has_fts_index(self) -> bool:
        if self._fts_index_created:
            return True
        if self._table is None:
            return False
        try:
            for idx in self._table.list_indices():
                if getattr(idx, "index_type", "") == "FTS":
                    self._fts_index_created = True
                    return True
        except Exception:
            pass
        return False

    def _create_fts_index_sync(self) -> None:
        try:
            logger.info("[LanceDBBackend] FTS index creation started (language=Chinese)")
            self._table.create_fts_index(
                self._INDEX_FTS_FIELD,
                replace=True,
                language="Chinese",
                lower_case=True,
                ascii_folding=True,
                max_token_length=64,
            )
            self._fts_index_created = True
            logger.info("[LanceDBBackend] FTS index creation completed")
        except Exception as e:
            logger.warning("[LanceDBBackend] FTS index creation failed: %s", e)
        finally:
            self._creating_fts_index = False

    def _create_fts_index_async(self) -> None:
        if self._creating_fts_index or self._fts_index_created:
            return
        if self._table is None or not self._enabled:
            return
        self._creating_fts_index = True
        t = threading.Thread(target=self._create_fts_index_sync, daemon=True)
        t.start()
        logger.debug("[LanceDBBackend] FTS index creation dispatched to background thread")

    def _ensure_table(self, embedding_dim: int) -> None:
        """创建表 (幂等)"""
        if self._table is not None:
            return
        if embedding_dim <= 0:
            return
        import pyarrow as pa

        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), embedding_dim)),
            pa.field("content", pa.string()),
            pa.field("metadata", pa.string()),
        ])
        self._table = self._db.create_table(
            self._table_name(), schema=schema, mode="overwrite",
        )
        self._embedding_dim = embedding_dim
        self._enabled = True
        logger.info(
            "[LanceDBBackend] Created table: dim=%d, path=%s",
            embedding_dim,
            self._persist_dir,
        )

    def _rebuild_for_dimension(self, new_dim: int) -> None:
        """维度变更时删除旧表并重建"""
        old_dim = self._embedding_dim
        table_name = self._table_name()
        try:
            with self._lock:
                self._table = None
                self._enabled = False
                if self._db and table_name in self._db.list_tables():
                    self._db.drop_table(table_name)
        except Exception as e:
            logger.warning("[LanceDBBackend] drop_table() during rebuild: %s", e)
            self._table = None
            self._enabled = False

        self._index_created = False
        self._creating_index = False
        self._fts_index_created = False
        self._creating_fts_index = False
        self._query_embed_cache.clear()

        try:
            self._ensure_table(new_dim)
        except Exception as e:
            logger.error(
                "[LanceDBBackend] Rebuild table failed for dim=%d: %s", new_dim, e
            )
            self._enabled = False
            return

        if self._table is not None:
            logger.info(
                "[LanceDBBackend] Table rebuilt for new dimension: %d → %d",
                old_dim,
                new_dim,
            )
            # 通知调用方（UnifiedStore）触发重新回填
            if self._on_rebuild is not None:
                try:
                    self._on_rebuild()
                except Exception as e:
                    logger.warning(
                        "[LanceDBBackend] on_rebuild callback failed: %s", e
                    )

    # ── Embedding Model ──

    def _get_embedder(self):
        """获取嵌入模型并做轻量探活"""
        if self._cached_embedder is not None and self._embedder_pinged:
            return self._cached_embedder
        try:
            from openakita.llm.embeddings import get_embedding_model

            model = get_embedding_model()
            if model is None:
                return None

            for _retry in range(3):
                try:
                    _ping_vec = _run_embedding_sync(model, "embed_query", "ping")
                    if _ping_vec and len(_ping_vec) > 0:
                        self._cached_embedder = model
                        self._embedder_pinged = True
                        self._mark_embedding_ok()

                        if self._table is None and model.dimension > 0:
                            try:
                                self._ensure_table(model.dimension)
                            except Exception:
                                pass

                        # 维度变更检测
                        if (
                            self._table is not None
                            and self._embedding_dim > 0
                            and model.dimension != self._embedding_dim
                        ):
                            logger.warning(
                                "[LanceDBBackend] Embedding dimension changed "
                                "(table=%d, model=%d); rebuilding table...",
                                self._embedding_dim,
                                model.dimension,
                            )
                            self._rebuild_for_dimension(model.dimension)

                        logger.debug(
                            "[LanceDBBackend] Embedding readiness ping OK (dim=%d)",
                            len(_ping_vec),
                        )
                        return model
                except Exception:
                    pass
                if _retry < 2:
                    import time
                    time.sleep(3 + _retry * 2)

            logger.error(
                "[LanceDBBackend] Embedding readiness ping failed after 3 retries"
            )
            self._mark_embedding_failure("readiness_ping")
            return None
        except Exception:
            return None

    # ── Health Tracking ──

    def _mark_embedding_ok(self) -> None:
        was_unhealthy = not self._embedding_healthy
        self._embedding_failures = 0
        self._embedding_healthy = True
        self._embedding_last_error = None
        if was_unhealthy:
            logger.info("[LanceDBBackend] Embedding recovered — vector search re-enabled")

    def _mark_embedding_failure(self, reason: str) -> None:
        self._embedding_failures += 1
        self._embedding_last_error = reason
        if self._embedding_failures >= 2:
            self._embedding_healthy = False
            logger.error(
                "[LanceDBBackend] Embedding unhealthy after %s failures (last: %s)",
                self._embedding_failures,
                reason,
            )
        else:
            logger.warning(
                "[LanceDBBackend] Embedding failure %s/2: %s",
                self._embedding_failures,
                reason,
            )

    # ── Query Embedding Cache ──

    def _get_cached_embedding(self, query: str) -> list[float] | None:
        if query in self._query_embed_cache:
            self._query_embed_cache.move_to_end(query)
            return self._query_embed_cache[query]
        return None

    def _cache_embedding(self, query: str, vec: list[float]) -> None:
        if query in self._query_embed_cache:
            self._query_embed_cache.move_to_end(query)
            self._query_embed_cache[query] = vec
            return
        if len(self._query_embed_cache) >= self._QUERY_EMBED_CACHE_SIZE:
            self._query_embed_cache.popitem(last=False)
        self._query_embed_cache[query] = vec

    # ── Warmup ──

    def warmup(self) -> bool:
        if self.available:
            return True
        if not self._lancedb:
            return False
        if self._get_embedder() is not None:
            self._enabled = True
            logger.info(
                "[LanceDBBackend] Warmup OK — embedding loaded, dim=%d",
                self._embedding_dim,
            )
            return True
        logger.warning("[LanceDBBackend] Warmup failed — embedding model not available")
        return False

    # ── SearchBackend Protocol ──

    @property
    def available(self) -> bool:
        return self._enabled and self._table is not None

    @property
    def backend_type(self) -> str:
        return "lancedb"

    def search(
        self,
        query: str,
        limit: int = 10,
        filter_type: str | None = None,
        scope: str | None = None,
        scope_owner: str | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        *,
        hybrid: bool = False,
    ) -> list[tuple[str, float]]:
        if not self.available:
            return []
        if not self._embedding_healthy:
            return []
        embedder = self._get_embedder()
        if embedder is None:
            return []

        query_vec = self._get_cached_embedding(query)
        if query_vec is None:
            try:
                query_vec = _run_embedding_sync(embedder, "embed_query", query)
            except Exception as e:
                self._mark_embedding_failure(str(e))
                return []
            if query_vec is None:
                self._mark_embedding_failure("timeout_or_none")
                return []
            if query_vec:
                self._cache_embedding(query, query_vec)

        self._mark_embedding_ok()

        if not query_vec:
            return []

        try:
            if hybrid and self._has_fts_index():
                with self._lock:
                    results = (
                        self._table.search(query_type="hybrid")
                        .text(query)
                        .vector(query_vec)
                        .metric(self._METRIC)
                        .limit(min(limit, 50))
                        .to_list()
                    )
                scored: list[tuple[str, float]] = []
                for row in results:
                    doc_id = row.get("id", "")
                    if not doc_id:
                        continue
                    rrf = float(row.get("_score", 0))
                    if math.isfinite(rrf):
                        scored.append((coerce_text(doc_id), rrf))
                return scored
            else:
                with self._lock:
                    results = (
                        self._table.search(query_vec)
                        .metric(self._METRIC)
                        .limit(min(limit, 50))
                        .to_list()
                    )
                if not results:
                    return []
                scored: list[tuple[str, float]] = []
                for row in results:
                    doc_id = row.get("id", "")
                    if not doc_id:
                        continue
                    distance = row.get("_distance", 1.0)
                    score = float(1.0 - distance / 2.0)
                    score = max(0.0, min(1.0, score))
                    if math.isfinite(score):
                        scored.append((coerce_text(doc_id), score))
                return scored
        except Exception as e:
            logger.warning(f"[LanceDBBackend] search failed: {e}")
            return []

    def add(self, memory_id: str, content: str, metadata: dict | None = None) -> bool:
        if not self._lancedb:
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
            self._mark_embedding_failure(str(e))
            return False

        if vec is None:
            self._mark_embedding_failure("add_timeout")
            return False

        self._mark_embedding_ok()

        if vec is not None and len(vec) != vec_dim:
            logger.warning(
                "[LanceDBBackend] Embedding dim mismatch: expected %d, got %d",
                vec_dim, len(vec),
            )
            vec_dim = len(vec)

        if self._table is not None and vec_dim != self._embedding_dim:
            logger.warning(
                "[LanceDBBackend] Dim mismatch with existing table: vector=%d, table=%d",
                vec_dim, self._embedding_dim,
            )

        with self._lock:
            if self._table is None and vec_dim > 0:
                try:
                    self._ensure_table(vec_dim)
                except Exception as e:
                    logger.warning(f"[LanceDBBackend] Auto-create table failed: {e}")
                    return False

            if self._table is None:
                return False

            try:
                meta_str = json.dumps(metadata or {}, ensure_ascii=False)
                self._table.add([{
                    "id": memory_id,
                    "vector": vec,
                    "content": content,
                    "metadata": meta_str,
                }])
                self._flush_table()
                self._create_index_async()
                self._create_fts_index_async()
                return True
            except Exception as e:
                logger.warning(
                    f"[LanceDBBackend] add failed for {memory_id[:8]}: {e}"
                )
                return False

    def delete(self, memory_id: str) -> bool:
        if not self.available:
            return False
        try:
            with self._lock:
                self._table.delete(f"id = '{memory_id}'")
            return True
        except Exception as e:
            logger.warning(
                f"[LanceDBBackend] delete failed for {memory_id[:8]}: {e}"
            )
            return False

    def batch_add(self, items: list[dict]) -> int:
        if not self._lancedb:
            return 0

        embedder = self._get_embedder()
        if embedder is None:
            return 0

        contents = [it.get("content", "") for it in items]
        try:
            vecs = _run_embedding_sync(embedder, "embed", contents)
        except Exception as e:
            self._mark_embedding_failure(str(e))
            return 0

        if vecs is None or len(vecs) != len(items) or any(not v for v in vecs):
            self._mark_embedding_failure("batch_add_failed")
            return 0

        self._mark_embedding_ok()

        vec_dim = getattr(embedder, "dimension", 0) or 0
        if vecs and vec_dim <= 0:
            vec_dim = len(vecs[0])
        if vecs and vec_dim != len(vecs[0]):
            logger.warning(
                "[LanceDBBackend] Embedding dim mismatch in batch: declared=%d, actual=%d",
                vec_dim, len(vecs[0]),
            )
            vec_dim = len(vecs[0])

        with self._lock:
            if self._table is None and vec_dim > 0:
                try:
                    self._ensure_table(vec_dim)
                except Exception as e:
                    logger.warning(f"[LanceDBBackend] Auto-create table failed: {e}")
                    return 0

            if self._table is None:
                return 0

            try:
                rows = []
                for i, it in enumerate(items):
                    doc_id = it.get("id", "")
                    if not doc_id:
                        continue
                    rows.append({
                        "id": doc_id,
                        "vector": vecs[i],
                        "content": it.get("content", ""),
                        "metadata": json.dumps(
                            it.get("metadata") or {}, ensure_ascii=False
                        ),
                    })
                if rows:
                    self._table.add(rows)
                    self._flush_table()
                self._create_index_async()
                self._create_fts_index_async()
                return len(rows)
            except Exception as e:
                logger.warning(f"[LanceDBBackend] batch_add failed: {e}")
                return 0

    def count(self) -> int:
        if not self.available:
            return 0
        try:
            with self._lock:
                return self._table.count_rows()
        except Exception as e:
            logger.warning("[LanceDBBackend] count failed: %s", e)
            return 0

    def episodes_count(self) -> int:
        if not self.episodes_available:
            return 0
        try:
            with self._lock:
                return self._episodes_table.count_rows()
        except Exception as e:
            logger.warning("[LanceDBBackend] episodes_count failed: %s", e)
            return 0

    def clear(self) -> bool:
        if not self._lancedb or self._table is None:
            return False
        try:
            with self._lock:
                table_name = self._table_name()
                if self._db and table_name in self._db.list_tables():
                    self._db.drop_table(table_name)
                    self._table = None
                    self._enabled = False
            return True
        except Exception as e:
            logger.warning(f"[LanceDBBackend] clear failed: {e}")
            return False

    def get_all_ids(self) -> set[str]:
        if not self.available:
            return set()
        try:
            with self._lock:
                tbl = self._table.to_arrow()
                ids_col = tbl.column("id")
                return {ids_col[i].as_py() for i in range(len(ids_col))}
        except Exception as e:
            logger.warning(f"[LanceDBBackend] get_all_ids failed: {e}")
            return set()

    def delete_not_in(self, keep_ids: set[str]) -> int:
        if not self.available:
            return 0
        try:
            existing = self.get_all_ids()
            stale = existing - keep_ids
            if not stale:
                return 0
            with self._lock:
                ids_str = ", ".join(f"'{id}'" for id in stale)
                self._table.delete(f"id IN ({ids_str})")
            logger.info(f"[LanceDBBackend] Removed {len(stale)} stale vectors")
            return len(stale)
        except Exception as e:
            logger.warning(f"[LanceDBBackend] delete_not_in failed: {e}")
            return 0

    def _flush_table(self) -> None:
        """每次 add 后调用，立即清理旧版本确保数据持久化到磁盘。"""
        table = self._table
        if table is None:
            return
        for method_name in ("cleanup_old_versions",):
            fn = getattr(table, method_name, None)
            if fn is None:
                continue
            try:
                fn()
            except Exception as e:
                logger.debug("[LanceDBBackend] %s skipped: %s", method_name, e)

    # ── Episodes Table ──

    @property
    def episodes_available(self) -> bool:
        return self._episodes_enabled and self._episodes_table is not None

    def _ensure_episodes_table(self, embedding_dim: int) -> None:
        if self._episodes_table is not None:
            return
        if embedding_dim <= 0 or self._db is None:
            return
        import pyarrow as pa

        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), embedding_dim)),
            pa.field("session_id", pa.string()),
            pa.field("summary", pa.string()),
            pa.field("goal", pa.string()),
            pa.field("started_at", pa.string()),
            pa.field("ended_at", pa.string()),
            pa.field("metadata", pa.string()),
        ])
        tbl_name = self._episodes_table_name()
        try:
            if tbl_name in self._db.list_tables():
                self._episodes_table = self._db.open_table(tbl_name)
            else:
                self._episodes_table = self._db.create_table(
                    tbl_name, schema=schema, mode="create",
                )
            self._episodes_enabled = True
            logger.info(
                "[LanceDBBackend] Opened or created episodes table: dim=%d",
                embedding_dim,
            )
        except Exception as e:
            logger.warning(
                "[LanceDBBackend] Failed to ensure episodes table: %s", e
            )
            self._episodes_table = None
            self._episodes_enabled = False

    def upsert_episode(
        self, episode_id: str, summary_text: str, meta: dict | None = None
    ) -> bool:
        if not self._lancedb:
            return False
        embedder = self._get_embedder()
        if embedder is None:
            return False

        vec_dim = getattr(embedder, "dimension", 0) or 0
        if vec_dim <= 0:
            return False

        try:
            vec = _run_embedding_sync(embedder, "embed_query", summary_text)
        except Exception as e:
            self._mark_embedding_failure(str(e))
            return False

        if vec is None:
            self._mark_embedding_failure("upsert_episode_timeout")
            return False

        if vec is not None and len(vec) != vec_dim:
            logger.warning(
                "[LanceDBBackend] upsert_episode dim mismatch: expected %d, got %d",
                vec_dim, len(vec),
            )
            vec_dim = len(vec)

        self._mark_embedding_ok()

        with self._lock:
            if self._episodes_table is None and vec_dim > 0:
                try:
                    self._ensure_episodes_table(vec_dim)
                except Exception as e:
                    logger.warning(
                        "[LanceDBBackend] Auto-create episodes table failed: %s", e
                    )
                    return False

            if self._episodes_table is None:
                return False

            # Check if table dimension matches embedder — rebuild if changed
            if self._episodes_table is not None:
                tbl_dim = self._read_table_dim_for(self._episodes_table)
                if tbl_dim > 0 and tbl_dim != vec_dim:
                    logger.warning(
                        "[LanceDBBackend] Episodes table dim changed: %d -> %d, rebuilding",
                        tbl_dim, vec_dim,
                    )
                    try:
                        self._db.drop_table(self._episodes_table_name())
                    except Exception:
                        pass
                    self._episodes_table = None
                    self._episodes_enabled = False
                    self._ensure_episodes_table(vec_dim)
                    if self._episodes_table is None:
                        return False

            try:
                meta_str = json.dumps(meta or {}, ensure_ascii=False)
                # Delete existing row with same id before insert
                try:
                    self._episodes_table.delete(f"id = '{episode_id}'")
                except Exception:
                    pass
                self._episodes_table.add([{
                    "id": episode_id,
                    "vector": vec,
                    "session_id": (meta or {}).get("session_id", ""),
                    "summary": summary_text,
                    "goal": (meta or {}).get("goal", ""),
                    "started_at": (meta or {}).get("started_at", ""),
                    "ended_at": (meta or {}).get("ended_at", ""),
                    "metadata": meta_str,
                }])
                try:
                    self._episodes_table.cleanup_old_versions()
                except Exception:
                    pass
                return True
            except Exception as e:
                logger.warning("[LanceDBBackend] upsert_episode failed: %s", e)
                return False

    def search_episodes(
        self, query_text: str, limit: int = 10
    ) -> list[dict]:
        if not self.episodes_available:
            return []
        if not self._embedding_healthy:
            return []
        embedder = self._get_embedder()
        if embedder is None:
            return []

        try:
            query_vec = _run_embedding_sync(embedder, "embed_query", query_text)
        except Exception as e:
            self._mark_embedding_failure(str(e))
            return []

        if query_vec is None:
            return []

        self._mark_embedding_ok()

        try:
            with self._lock:
                results = (
                    self._episodes_table.search(query_vec)
                    .metric(self._METRIC)
                    .limit(min(limit, 20))
                    .to_list()
                )
            if not results:
                return []
            out = []
            for row in results:
                doc_id = row.get("id", "")
                if not doc_id:
                    continue
                distance = row.get("_distance", 1.0)
                score = float(1.0 - distance / 2.0)
                score = max(0.0, min(1.0, score))
                out.append({
                    "id": doc_id,
                    "session_id": row.get("session_id", ""),
                    "summary": row.get("summary", ""),
                    "goal": row.get("goal", ""),
                    "started_at": row.get("started_at", ""),
                    "ended_at": row.get("ended_at", ""),
                    "_score": score,
                })
            return out
        except Exception as e:
            logger.warning("[LanceDBBackend] search_episodes failed: %s", e)
            return []

    def delete_episode(self, episode_id: str) -> bool:
        if not self.episodes_available:
            return False
        try:
            with self._lock:
                self._episodes_table.delete(f"id = '{episode_id}'")
            return True
        except Exception as e:
            logger.warning("[LanceDBBackend] delete_episode failed: %s", e)
            return False

    def close(self) -> None:
        """关闭 LanceDB 连接并 compact 数据文件，防止 Windows 重启后损坏。"""
        for tbl_attr in ("_table", "_episodes_table"):
            table = getattr(self, tbl_attr, None)
            if table is not None:
                for method_name in ("optimize", "compact_files", "cleanup_old_versions"):
                    fn = getattr(table, method_name, None)
                    if fn is None:
                        continue
                    try:
                        fn()
                        logger.info("[LanceDBBackend] %s %s completed", tbl_attr, method_name)
                    except Exception as e:
                        logger.debug("[LanceDBBackend] %s %s skipped: %s", tbl_attr, method_name, e)
                setattr(self, tbl_attr, None)
        if self._db is not None:
            try:
                self._db = None
            except Exception:
                pass
        self._enabled = False
        self._episodes_enabled = False
        gc.collect()
        logger.info("[LanceDBBackend] connection closed")
