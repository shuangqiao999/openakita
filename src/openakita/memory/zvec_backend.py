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

    # 检测是否在当前线程的运行中事件循环内（会死锁的情况）
    try:
        asyncio.get_running_loop()
        # 当前线程有运行中的事件循环 → 在独立线程中执行以脱锁
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(asyncio.run, method(*args))
            try:
                return future.result(timeout=_EMBEDDING_TIMEOUT_SEC)
            except concurrent.futures.TimeoutError:
                logger.error(
                    "[ZvecBackend] Embedding call timed out after %ds",
                    _EMBEDDING_TIMEOUT_SEC,
                )
                return None
            except Exception as e:
                logger.error("[ZvecBackend] Embedding call failed: %s", e)
                return None
    except RuntimeError:
        pass  # 无运行中的循环，继续走原有路径

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
                "[ZvecBackend] Embedding call timed out after %ds",
                _EMBEDDING_TIMEOUT_SEC,
            )
            return None
        except Exception as e:
            logger.error("[ZvecBackend] Embedding call failed: %s", e)
            return None
    else:
        return asyncio.run(method(*args))


def _remove_lock_path(lock_path: Path) -> bool:
    """Remove a stale zvec LOCK file/directory with retry.

    On Windows, zvec/LMDB LOCK can be a *directory* rather than a regular
    file, and ``unlink()`` may fail transiently due to antivirus or FS
    caching.  Use ``rmtree`` for dirs, and retry up to 3 times with a
    small backoff.
    """
    import shutil
    import time

    for attempt in range(3):
        try:
            if lock_path.is_dir():
                shutil.rmtree(lock_path)
            else:
                lock_path.unlink()
            if not lock_path.exists():
                return True
        except OSError:
            pass
        if attempt < 2:
            time.sleep(0.3 * (attempt + 1))
    return False


def _remove_corrupt_collection(coll_path: str) -> bool:
    """Remove a corrupted zvec collection directory with retry.

    Windows 文件系统异步删除（antivirus / FS caching）可能导致
    ``shutil.rmtree`` 返回后目录仍存在。外层循环反复尝试 rmtree +
    轮询确认，直到目录真正消失或超过最大重试次数。

    Returns:
        True if the collection directory was successfully removed.
    """
    import shutil
    import time

    coll = Path(coll_path)
    max_attempts = 5
    poll_interval = 0.5
    poll_cycles = 40  # max 20s per rmtree attempt

    for attempt in range(max_attempts):
        if not coll.is_dir():
            return True

        try:
            shutil.rmtree(coll_path, ignore_errors=True)
        except Exception as e:
            logger.warning(
                "[ZvecBackend] rmtree attempt %d/%d threw: %s",
                attempt + 1,
                max_attempts,
                e,
            )

        # 轮询等待目录消失
        for _ in range(poll_cycles):
            if not coll.is_dir():
                logger.info(
                    "[ZvecBackend] Corrupt collection removed "
                    "(attempt %d/%d)",
                    attempt + 1,
                    max_attempts,
                )
                return True
            time.sleep(poll_interval)

        if attempt < max_attempts - 1:
            logger.warning(
                "[ZvecBackend] Directory still exists after rmtree+poll "
                "(attempt %d/%d), retrying...",
                attempt + 1,
                max_attempts,
            )
            time.sleep(1.0)

    return False


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
        # 嵌入健康门控: 连续失败 N 次后自动跳过 embedding（触发 FTS5 降级）
        self._embedding_failures = 0
        self._embedding_healthy = True
        self._embedding_last_error: str | None = None

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
                self._open_collection(coll_path, embedding_dim)
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

    def _open_collection(self, coll_path: str, embedding_dim: int = 0) -> None:
        """打开已有 collection，处理残留 LOCK 文件和 API 兼容性

        zvec 内部 lock 获取有 ~60s 阻塞超时（LMDB/MDB_LOCK_TIMEOUT），
        若上次崩溃遗留 LOCK 文件未清，会白等 60s。此处在调用 zvec.open()
        之前先主动检测并清除残留 LOCK，将 O(60s) 降为 O(1ms)。

        Windows 上 zvec LOCK 可能是目录（LMDB），需 rmtree 而非 unlink。
        LMDB LOCK 可能出现在子目录（如 idmap.0/LOCK, 0/LOCK），因此
        递归扫描整个 collection 目录。

        打开失败时根据 LOCK 清理结果走三条路径：
        1. 所有 LOCK 已清除但 open 仍失败 → 真正损坏 → 删除+重建
        2. 有 LOCK 无法清除 → 其他实例正使用此 collection → 跳过，不禁用
        3. 无 LOCK 文件且 open 成功 → 正常打开
        """
        lock_stale = False
        any_lock_failed = False
        coll_path_obj = Path(coll_path)

        for lock_path in sorted(coll_path_obj.rglob("LOCK"), reverse=True):
            lock_stale = True
            removed = _remove_lock_path(lock_path)
            if removed:
                logger.warning(
                    "[ZvecBackend] Stale LOCK removed at %s (avoids ~60s lock wait)",
                    lock_path,
                )
            else:
                any_lock_failed = True
                logger.warning(
                    "[ZvecBackend] Failed to remove stale LOCK at %s, "
                    "zvec.open() may block up to 60s",
                    lock_path,
                )

        try:
            self._collection = self._zvec.open(path=coll_path)
        except Exception as e:
            logger.warning("[ZvecBackend] Open collection failed: %s", e)
            if any_lock_failed:
                logger.info(
                    "[ZvecBackend] LOCK removal was blocked — another instance "
                    "is likely using this collection; zvec will be skipped "
                    "for this instance (vector search falls back to FTS5). "
                    "Collection dir: %s",
                    coll_path,
                )
                self._enabled = False
                return
            if embedding_dim > 0 and Path(coll_path).is_dir():
                logger.warning(
                    "[ZvecBackend] All LOCKs removed but open still failed; "
                    "collection appears corrupted, deleting and rebuilding: %s",
                    coll_path,
                )
                removed = _remove_corrupt_collection(coll_path)
                if not removed:
                    logger.error(
                        "[ZvecBackend] Failed to remove corrupted collection "
                        "after repeated attempts; zvec will be disabled. "
                        "To recover, manually delete: %s",
                        coll_path,
                    )
                    self._enabled = False
                    return
                self._ensure_collection(coll_path, embedding_dim)
                return
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

    def _rebuild_for_dimension(self, new_dim: int) -> None:
        """删除旧 collection 并以新维度重建。

        调用方已通过 logger.warning 报告维度变更。
        重建后 self._collection / self._embedding_dim / self._enabled
        由 self._ensure_collection 设置。
        失败时禁用 zvec，后续请求回退到 FTS5。
        """
        import shutil

        coll_path = self._coll_path()
        old_dim = self._embedding_dim
        try:
            with self._lock:
                if self._collection is not None:
                    self._collection.destroy()
                    self._collection = None
                self._enabled = False
        except Exception as e:
            logger.warning("[ZvecBackend] destroy() failed during rebuild: %s", e)
            self._collection = None
            self._enabled = False

        # 轮询删除目录（Windows 异步删除可能延迟）
        if Path(coll_path).is_dir():
            try:
                shutil.rmtree(coll_path, ignore_errors=True)
            except Exception:
                pass
            for _ in range(40):
                if not Path(coll_path).is_dir():
                    break
                import time
                time.sleep(0.5)

        try:
            self._ensure_collection(coll_path, new_dim)
        except Exception as e:
            logger.error(
                "[ZvecBackend] Rebuild collection failed for dim=%d: %s", new_dim, e
            )
            self._enabled = False
            return

        if self._collection is not None:
            logger.info(
                "[ZvecBackend] Collection rebuilt for new dimension: %d → %d",
                old_dim,
                new_dim,
            )

    @property
    def available(self) -> bool:
        return self._enabled and self._collection is not None

    @property
    def backend_type(self) -> str:
        return "zvec"

    def _get_embedder(self):
        """获取嵌入模型并做轻量探活。

        首次调用时发送轻量 ping 验证 embedding 服务可达，失败则
        标记 unhealthy 并返回 None（触发 FTS5 降级）。成功后将结果缓存，
        后续调用直接返回。
        """
        if self._cached_embedder is not None and getattr(self, "_embedder_pinged", False):
            return self._cached_embedder
        try:
            from openakita.llm.embeddings import get_embedding_model

            model = get_embedding_model()
            if model is None:
                return None

            # 启动就绪探活: 发轻量 embedding 验证 LMStudio/embedding 服务可达
            for _retry in range(3):
                try:
                    _ping_vec = _run_embedding_sync(model, "embed_query", "ping")
                    if _ping_vec and len(_ping_vec) > 0:
                        self._cached_embedder = model
                        self._embedder_pinged = True  # type: ignore[attr-defined]
                        self._mark_embedding_ok()

                        # 嵌入模型就绪但 collection 未创建时，惰性初始化
                        if self._collection is None and model.dimension > 0:
                            try:
                                self._ensure_collection(
                                    self._coll_path(), model.dimension
                                )
                            except Exception:
                                pass

                        # 维度变更检测：已有 collection 但 schema 维度与新模型不匹配
                        # 自动删除旧 collection 并重建；UnifiedStore backfill 线程会从 SQLite 重新索引
                        if (
                            self._collection is not None
                            and self._embedding_dim > 0
                            and model.dimension != self._embedding_dim
                        ):
                            logger.warning(
                                "[ZvecBackend] Embedding dimension changed "
                                "(collection=%d, model=%d); "
                                "rebuilding collection...",
                                self._embedding_dim,
                                model.dimension,
                            )
                            self._rebuild_for_dimension(model.dimension)

                        logger.debug(
                            "[ZvecBackend] Embedding readiness ping OK (dim=%d)",
                            len(_ping_vec),
                        )
                        return model
                except Exception:
                    pass
                if _retry < 2:
                    import time
                    time.sleep(3 + _retry * 2)

            logger.error(
                "[ZvecBackend] Embedding readiness ping failed after 3 retries — "
                "vector search will be skipped"
            )
            self._mark_embedding_failure("readiness_ping")
            return None
        except Exception:
            return None

    def _coll_path(self) -> str:
        return str(self._persist_dir / "@openakita_memories")

    # ── SearchBackend Protocol 实现 (文本接口) ──

    def _check_embedding_health(self) -> bool:
        """Returns False if embedding is known unhealthy (skip vector search)."""
        if self._embedding_failures >= 2:
            self._embedding_healthy = False
            return False
        return True

    def _mark_embedding_ok(self) -> None:
        """Reset failure counter on successful embedding call."""
        was_unhealthy = not self._embedding_healthy
        self._embedding_failures = 0
        self._embedding_healthy = True
        self._embedding_last_error = None
        if was_unhealthy:
            logger.info("[ZvecBackend] Embedding recovered — vector search re-enabled")

    def _mark_embedding_failure(self, reason: str) -> None:
        """Increment failure counter; disable vector search after 2 consecutive failures."""
        self._embedding_failures += 1
        self._embedding_last_error = reason
        if self._embedding_failures >= 2:
            self._embedding_healthy = False
            logger.error(
                "[ZvecBackend] Embedding unhealthy after %s failures (last: %s) — "
                "vector search disabled, FTS5 will be used",
                self._embedding_failures,
                reason,
            )
        else:
            logger.warning(
                "[ZvecBackend] Embedding failure %s/2: %s",
                self._embedding_failures,
                reason,
            )

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
        if not self._embedding_healthy:
            return []
        embedder = self._get_embedder()
        if embedder is None:
            return []
        try:
            query_vec = _run_embedding_sync(embedder, "embed_query", query)
        except Exception as e:
            self._mark_embedding_failure(str(e))
            return []

        if query_vec is None:
            # Timeout or embedding call failed — mark unhealthy
            self._mark_embedding_failure("timeout_or_none")
            return []

        # Embedding succeeded — reset health
        self._mark_embedding_ok()

        if not query_vec:
            return []

        try:
            # 兼容两个 zvec API 版本:
            #   bundled: zvec.Query(name=..., vector=...), queries=kwargs
            #   pip 0.4.0: zvec.VectorQuery(name=..., vector=...), positional arg
            query_cls = getattr(self._zvec, "Query", None) or self._zvec.VectorQuery
            zq = query_cls(field_name=self._EMBEDDING_FIELD, vector=query_vec)
            with self._lock:
                try:
                    results = self._collection.query(
                        queries=zq, topk=min(limit, 50), include_vector=False
                    )
                except TypeError:
                    results = self._collection.query(zq, topk=min(limit, 50))
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
            self._mark_embedding_failure(str(e))
            return False

        if vec is None:
            self._mark_embedding_failure("add_timeout")
            return False

        self._mark_embedding_ok()

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
            self._mark_embedding_failure(str(e))
            return 0

        if vecs is None or len(vecs) != len(items) or any(not v for v in vecs):
            self._mark_embedding_failure("batch_add_failed")
            return 0

        self._mark_embedding_ok()

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
                if not stats:
                    return 0
                return (
                    getattr(stats, "row_count", None)
                    or getattr(stats, "doc_count", 0)
                )
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
