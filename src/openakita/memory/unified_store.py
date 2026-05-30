"""
统一存储层

协调 MemoryStorage (SQLite) + SearchBackend (搜索引擎):
- 写入: SQLite 主写 + SearchBackend 索引同步
- 查询: 结构化查询走 SQLite, 语义搜索走 SearchBackend
- 降级: SearchBackend 不可用时回退到 FTS5
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .search_backends import FTS5Backend, SearchBackend, create_search_backend
from .storage import get_shared_storage
from .types import (
    Attachment,
    Episode,
    Scratchpad,
    SemanticMemory,
)

logger = logging.getLogger(__name__)


class UnifiedStore:
    """统一存储层: SQLite 为主存储, SearchBackend 为搜索引擎"""

    def __init__(
        self,
        db_path: str | Path,
        search_backend: SearchBackend | None = None,
        *,
        vector_store: Any = None,
        backend_type: str = "lancedb",
        api_provider: str = "",
        api_key: str = "",
        api_model: str = "",
    ) -> None:
        self.db = get_shared_storage(db_path)

        if search_backend is not None:
            self.search = search_backend
        else:
            # 注册维度变更重建回调：旧向量表被删除后，从 SQLite 重新索引
            def _on_rebuild_trigger():
                self._backfill_started = False
                self._needs_full_backfill = True
                self._backfill_semantic_if_empty()

            self.search = create_search_backend(
                backend_type,
                storage=self.db,
                vector_store=vector_store,
                api_provider=api_provider,
                api_key=api_key,
                api_model=api_model,
                on_rebuild=_on_rebuild_trigger,
            )

        self._fts5_fallback: FTS5Backend | None = None
        if self.search.backend_type != "fts5":
            self._fts5_fallback = FTS5Backend(self.db)

        self._backfill_semantic_if_empty()
        self._backfill_episodes_if_needed()

    def _backfill_semantic_if_empty(self) -> None:
        """若语义搜索后端已可用但 vector count 为 0，在后台线程中从 SQLite 回填已有记忆。

        不阻塞启动流程。回填期间语义搜索降级到 FTS5。
        使用 daemon 线程避免阻止进程退出，_backfill_started 标记防止重复触发。
        """
        if self.search.backend_type == "fts5":
            return
        if not self.search.available:
            return
        if getattr(self, "_backfill_started", False):
            return
        self._backfill_started = True

        import threading

        thread = threading.Thread(target=self._backfill_worker, daemon=True, name="semantic-backfill")
        thread.start()
        logger.info("[UnifiedStore] Semantic backfill thread started (daemon)")

    def _backfill_worker(self) -> None:
        """后台回填工作函数：从 SQLite 查询记忆并批量写入语义搜索后端。

        在独立线程中执行，不阻塞主启动流程。
        batch_add() 内部调用 _run_embedding_sync 桥接到异步嵌入事件循环。

        支持断点续传：通过 get_all_ids() 跳过已存在的记录，避免维度重建中断后重复工作。
        """
        try:
            count_fn = getattr(self.search, "count", None)
            if count_fn is None:
                return
            existing = count_fn()
            needs_full = getattr(self, "_needs_full_backfill", False)
            if existing > 0 and not needs_full:
                logger.debug(
                    "[UnifiedStore] Backfill skipped: already has %d vectors", existing
                )
                return
        except Exception:
            return

        try:
            all_mems = self.db.query(limit=5000, active_only=False)
        except Exception as e:
            logger.warning("[UnifiedStore] Backfill: failed to query SQLite: %s", e)
            return

        if not all_mems:
            return

        # 断点续传：跳过已存在于向量库中的记录
        skip_ids: set[str] = set()
        if needs_full and hasattr(self.search, "get_all_ids"):
            try:
                skip_ids = self.search.get_all_ids()
                if skip_ids:
                    logger.info(
                        "[UnifiedStore] Backfill resume: %d already indexed, skipping",
                        len(skip_ids),
                    )
            except Exception:
                pass

        logger.info("[UnifiedStore] Backfill started: %d memories to index", len(all_mems))

        batch_size = 50
        total_indexed = 0
        for offset in range(0, len(all_mems), batch_size):
            batch = all_mems[offset : offset + batch_size]
            items: list[dict] = []
            for mem in batch:
                mem_id = mem.get("id", "")
                content = mem.get("content", "")
                if not mem_id or not content:
                    continue
                items.append(
                    {
                        "id": mem_id,
                        "content": content,
                        "metadata": {
                            "type": mem.get("type", "fact"),
                            "priority": mem.get("priority", "short_term"),
                            "importance": mem.get("importance_score", 0.5),
                            "tags": mem.get("tags", []),
                        },
                    }
                )
            # 断点续传：跳过已存在的记录
            if skip_ids:
                items = [it for it in items if it["id"] not in skip_ids]
            if not items:
                continue
            try:
                n = self.search.batch_add(items)
                total_indexed += n
                if n < len(items):
                    logger.warning(
                        "[UnifiedStore] Backfill batch partial: %d/%d (offset=%d)",
                        n, len(items), offset,
                    )
            except Exception as e:
                logger.error(
                    "[UnifiedStore] Backfill batch failed (offset=%d): %s", offset, e
                )

        logger.info(
            "[UnifiedStore] Backfill completed: %d/%d memories indexed into semantic backend",
            total_indexed,
            len(all_mems),
        )
        self._needs_full_backfill = False

    # ======================================================================
    # Episodes vector backfill
    # ======================================================================

    def _backfill_episodes_if_needed(self) -> None:
        """若 SQLite episodes 数量与 LanceDB 差距过大，后台补写缺失的向量。

        比例检查：当 LanceDB/SQLite < 95% 时触发，覆盖单条写入失败后的累积缺失。
        daemon 线程，不阻塞启动。嵌入模型不可用时静默跳过。
        """
        if self.search.backend_type == "fts5":
            return
        if not getattr(self.search, "episodes_available", False):
            return
        sqlite_count = self.count_episodes()
        if sqlite_count == 0:
            return
        lance_count = self.search.episodes_count()
        if lance_count >= sqlite_count * 0.95:
            return

        import threading

        thread = threading.Thread(
            target=self._backfill_episodes_worker, daemon=True, name="episodes-backfill",
        )
        thread.start()
        logger.info("[UnifiedStore] Episodes backfill thread started (daemon)")

    def _backfill_episodes_worker(self) -> None:
        """后台回填工作函数：从 SQLite 读取 episodes 并逐条写入 LanceDB。

        异常不中断流程，单条失败跳过继续。
        """
        try:
            episodes = self.get_recent_episodes(days=9999, limit=50000)
        except Exception as e:
            logger.warning("[UnifiedStore] Episodes backfill: query failed: %s", e)
            return

        if not episodes:
            return

        logger.info("[UnifiedStore] Episodes backfill started: %d episodes", len(episodes))
        total_done = 0
        total_skipped = 0

        for ep in episodes:
            try:
                summary = ep.summary or ep.goal or ""
                if not summary.strip():
                    total_skipped += 1
                    continue
                meta = {
                    "session_id": ep.session_id,
                    "goal": ep.goal,
                    "outcome": ep.outcome,
                    "started_at": ep.started_at.isoformat() if ep.started_at else "",
                    "ended_at": ep.ended_at.isoformat() if ep.ended_at else "",
                    "tags": ep.tags or [],
                    "entities": ep.entities or [],
                }
                ok = self.search.upsert_episode(ep.id, summary, meta)
                if ok:
                    total_done += 1
                else:
                    total_skipped += 1
            except Exception:
                total_skipped += 1

        logger.info(
            "[UnifiedStore] Episodes backfill completed: %d indexed, %d skipped (total %d)",
            total_done, total_skipped, len(episodes),
        )

    @staticmethod
    def _is_active_dict(memory: dict) -> bool:
        if memory.get("superseded_by"):
            return False
        expires_at = memory.get("expires_at")
        if expires_at:
            try:
                if datetime.fromisoformat(expires_at) < datetime.now():
                    return False
            except Exception:
                return False
        return True

    # ======================================================================
    # Semantic Memory
    # ======================================================================

    def save_semantic(
        self,
        memory: SemanticMemory,
        scope: str = "user",
        scope_owner: str = "",
        user_id: str = "default",
        workspace_id: str = "default",
        *,
        skip_dedup: bool = False,
    ) -> str:
        memory.scope = scope
        memory.scope_owner = scope_owner
        memory.user_id = user_id or "default"
        memory.workspace_id = workspace_id or "default"

        if not skip_dedup and memory.content and len(memory.content.strip()) > 10:
            try:
                dup_id = self._check_semantic_duplicate(
                    memory.content,
                    scope,
                    scope_owner,
                    memory.user_id,
                    memory.workspace_id,
                )
                if dup_id:
                    logger.debug(
                        "[UnifiedStore] Skipping duplicate memory: new='%s…' matches existing %s",
                        memory.content[:40],
                        dup_id,
                    )
                    return dup_id
            except Exception:
                pass

        d = memory.to_dict()
        self.db.save_memory(d)
        self.search.add(
            memory.id,
            memory.content,
            {
                "type": memory.type.value,
                "priority": memory.priority.value,
                "importance": memory.importance_score,
                "tags": memory.tags,
            },
        )
        return memory.id

    def _check_semantic_duplicate(
        self,
        content: str,
        scope: str,
        scope_owner: str,
        user_id: str,
        workspace_id: str,
    ) -> str | None:
        """Return existing memory ID if *content* is near-duplicate, else None."""
        core = content.strip()[:100].lower()
        hits = self.search.search(
            core,
            limit=5,
            scope=scope,
            scope_owner=scope_owner,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        # FTS5 fallback only when primary search returned nothing
        if not hits and self._fts5_fallback is not None:
            hits = self._fts5_fallback.search(
                core,
                limit=5,
                scope=scope,
                scope_owner=scope_owner,
                user_id=user_id,
                workspace_id=workspace_id,
            )
        for mid, _score in hits:
            existing = self.db.get_memory(mid)
            if not existing:
                continue
            if not self._is_active_dict(existing):
                continue
            if (existing.get("scope") or "global") != scope:
                continue
            if (existing.get("scope_owner") or "") != scope_owner:
                continue
            if (existing.get("user_id") or "default") != user_id:
                continue
            if (existing.get("workspace_id") or "default") != workspace_id:
                continue
            ec = (existing.get("content") or "").strip().lower()
            if core[:80] in ec or ec[:80] in core:
                return mid
        return None

    def update_semantic(self, memory_id: str, updates: dict) -> bool:
        ok = self.db.update_memory(memory_id, updates)
        reindex_fields = {"content", "type", "priority", "importance_score", "tags"}
        if ok and reindex_fields.intersection(updates):
            self.search.delete(memory_id)
            mem = self.db.get_memory(memory_id)
            if mem and self._is_active_dict(mem):
                self.search.add(
                    memory_id,
                    mem["content"],
                    {
                        "type": mem.get("type", "fact"),
                        "priority": mem.get("priority", "short_term"),
                        "importance": mem.get("importance_score", 0.5),
                        "tags": mem.get("tags", []),
                    },
                )
        return ok

    def delete_semantic(self, memory_id: str) -> bool:
        self.search.delete(memory_id)
        return self.db.delete_memory(memory_id)

    def cleanup_expired(self) -> int:
        expired_ids = self.db.get_expired_memory_ids()
        count = self.db.cleanup_expired()
        for memory_id in expired_ids:
            self.search.delete(memory_id)
        return count

    def bump_access(self, memory_ids: list[str]) -> None:
        """Batch-increment access_count for memories confirmed useful by LLM."""
        if not memory_ids:
            return
        now = datetime.now().isoformat()
        for mid in memory_ids:
            self.db.update_memory(
                mid,
                {
                    "access_count": (self.db.get_memory(mid) or {}).get("access_count", 0) + 1,
                    "last_accessed_at": now,
                },
            )

    def get_semantic(self, memory_id: str, *, include_inactive: bool = False) -> SemanticMemory | None:
        d = self.db.get_memory(memory_id)
        if d is None:
            return None
        if not include_inactive and not self._is_active_dict(d):
            return None
        self.db.update_memory(
            memory_id,
            {
                "access_count": d.get("access_count", 0) + 1,
                "last_accessed_at": datetime.now().isoformat(),
            },
        )
        return SemanticMemory.from_dict(d)

    def search_semantic(
        self,
        query: str,
        limit: int = 10,
        filter_type: str | None = None,
        scope: str = "user",
        scope_owner: str = "",
        user_id: str = "default",
        workspace_id: str = "default",
        include_inactive: bool = False,
    ) -> list[SemanticMemory]:
        scored = self.search_semantic_scored(
            query,
            limit=limit,
            filter_type=filter_type,
            scope=scope,
            scope_owner=scope_owner,
            user_id=user_id,
            workspace_id=workspace_id,
            include_inactive=include_inactive,
        )
        return [mem for mem, _score in scored]

    def search_semantic_scored(
        self,
        query: str,
        limit: int = 10,
        filter_type: str | None = None,
        scope: str = "user",
        scope_owner: str = "",
        user_id: str = "default",
        workspace_id: str = "default",
        include_inactive: bool = False,
    ) -> list[tuple[SemanticMemory, float]]:
        """Like search_semantic but also returns the raw similarity score.

        LanceDB backend uses native hybrid search (vector + FTS via RRF) with
        Chinese-tokenized FTS index for CJK coverage.  FTS5 only activates as
        fallback when LanceDB is unavailable.
        """
        use_hybrid = self.search.backend_type == "lancedb"

        primary = self.search.search(
            query,
            limit=limit * 3,
            filter_type=filter_type,
            scope=scope,
            scope_owner=scope_owner,
            user_id=user_id,
            workspace_id=workspace_id,
            hybrid=use_hybrid,
        )

        # When LanceDB hybrid returns results, use them directly.
        # FTS5 is only a fallback when LanceDB is unavailable.
        if primary:
            merged: dict[str, float] = {mid: float(s) for mid, s in primary}
        elif self._fts5_fallback is not None:
            # LanceDB unavailable → fall back to FTS5
            try:
                fts_results = self._fts5_fallback.search(
                    query,
                    limit=limit * 3,
                    filter_type=filter_type,
                    scope=scope,
                    scope_owner=scope_owner,
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
                merged = {mid: float(s) for mid, s in fts_results}
                logger.debug(
                    "[HybridSearch] LanceDB unavailable, fts5=%d fallback", len(merged)
                )
            except Exception as _e:
                logger.debug("[UnifiedStore] FTS5 fallback failed: %s", _e)
                merged = {}
        else:
            merged = {}

        if not merged:
            return []

        ordered = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)

        # Batch-load all candidates from SQLite in a single query
        candidate_ids = [mid for mid, _s in ordered]
        mem_map = self.db.get_memories_batch(candidate_ids)

        scored: list[tuple[SemanticMemory, float]] = []
        for memory_id, score in ordered:
            d = mem_map.get(memory_id)
            if not d:
                continue
            if not include_inactive and not self._is_active_dict(d):
                continue
            if (
                (d.get("scope") or "global") == scope
                and (d.get("scope_owner") or "") == scope_owner
                and (d.get("user_id") or "default") == user_id
                and (d.get("workspace_id") or "default") == workspace_id
            ):
                scored.append((SemanticMemory.from_dict(d), float(score)))
                if len(scored) >= limit:
                    break
        return scored

    def query_semantic(self, **kwargs: Any) -> list[SemanticMemory]:
        include_inactive = bool(kwargs.pop("include_inactive", False))
        kwargs.setdefault("active_only", not include_inactive)
        rows = self.db.query(**kwargs)  # scope/scope_owner pass through via kwargs
        return [SemanticMemory.from_dict(r) for r in rows]

    def find_similar(
        self,
        subject: str,
        predicate: str,
        scope: str = "user",
        scope_owner: str = "",
        user_id: str = "default",
        workspace_id: str = "default",
    ) -> SemanticMemory | None:
        """Find existing memory with same subject+predicate for update detection."""
        rows = self.db.query(
            subject=subject,
            scope=scope,
            scope_owner=scope_owner,
            user_id=user_id,
            workspace_id=workspace_id,
            limit=10,
        )
        for row in rows:
            if row.get("predicate", "").lower() == predicate.lower():
                return SemanticMemory.from_dict(row)
        query = f"{subject} {predicate}"
        results = self.search.search(
            query,
            limit=5,
            scope=scope,
            scope_owner=scope_owner,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        for mid, score in results:
            if score > 0.8:
                d = self.db.get_memory(mid)
                if (
                    d
                    and self._is_active_dict(d)
                    and d.get("subject", "").lower() == subject.lower()
                ):
                    d_scope = d.get("scope") or "global"
                    d_owner = d.get("scope_owner") or ""
                    d_user = d.get("user_id") or "default"
                    d_workspace = d.get("workspace_id") or "default"
                    if (
                        d_scope == scope
                        and d_owner == scope_owner
                        and d_user == user_id
                        and d_workspace == workspace_id
                    ):
                        return SemanticMemory.from_dict(d)
        return None

    def count_memories(
        self,
        memory_type: str | None = None,
        scope: str | None = None,
        scope_owner: str | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        include_inactive: bool = False,
    ) -> int:
        return self.db.count(
            memory_type,
            scope=scope,
            scope_owner=scope_owner,
            user_id=user_id,
            workspace_id=workspace_id,
            active_only=not include_inactive,
        )

    def load_all_memories(
        self,
        scope: str | None = None,
        scope_owner: str | None = None,
        *,
        user_id: str | None = None,
        workspace_id: str | None = None,
        include_inactive: bool = False,
    ) -> list[SemanticMemory]:
        rows = self.db.load_all(
            scope=scope,
            scope_owner=scope_owner,
            user_id=user_id,
            workspace_id=workspace_id,
            active_only=not include_inactive,
        )
        return [SemanticMemory.from_dict(r) for r in rows]

    def query_paged(self, **kwargs: Any) -> tuple[list[SemanticMemory], int]:
        """Paginated query delegating to storage.query_paged()."""
        include_inactive = bool(kwargs.pop("include_inactive", False))
        kwargs.setdefault("active_only", not include_inactive)
        rows, total = self.db.query_paged(**kwargs)
        return [SemanticMemory.from_dict(r) for r in rows], total

    # ======================================================================
    # Episode Memory
    # ======================================================================

    def save_episode(self, episode: Episode) -> str:
        self.db.save_episode(episode.to_dict())
        backend = getattr(self, "search", None)
        if backend is not None and hasattr(backend, "upsert_episode"):
            try:
                meta = {
                    "session_id": episode.session_id,
                    "goal": episode.goal,
                    "started_at": episode.started_at.isoformat() if episode.started_at else "",
                    "ended_at": episode.ended_at.isoformat() if episode.ended_at else "",
                    "outcome": episode.outcome,
                    "tags": episode.tags,
                }
                backend.upsert_episode(
                    episode.id,
                    episode.summary or episode.goal or "",
                    meta,
                )
            except Exception:
                logger.debug("[UnifiedStore] LanceDB episode sync skipped", exc_info=True)
        return episode.id

    def get_episode(self, episode_id: str) -> Episode | None:
        d = self.db.get_episode(episode_id)
        return Episode.from_dict(d) if d else None

    def search_episodes(self, **kwargs: Any) -> list[Episode]:
        rows = self.db.search_episodes(**kwargs)
        return [Episode.from_dict(r) for r in rows]

    def get_recent_episodes(self, days: int = 7, limit: int = 10) -> list[Episode]:
        return self.search_episodes(days=days, limit=limit)

    def update_episode(self, episode_id: str, updates: dict) -> bool:
        return self.db.update_episode(episode_id, updates)

    def link_turns_to_episode(self, session_id: str, episode_id: str) -> int:
        return self.db.link_turns_to_episode(session_id, episode_id)

    def count_episodes(self) -> int:
        return self.db.count_episodes()

    def search_episodes_fts(
        self, query: str, days_back: int = 7, limit: int = 10
    ) -> list[Episode]:
        rows = self.db.search_episodes_fts(query, days_back=days_back, limit=limit)
        return [Episode.from_dict(r) for r in rows]

    # ======================================================================
    # Scratchpad
    # ======================================================================

    def get_scratchpad(self, user_id: str = "default") -> Scratchpad | None:
        d = self.db.get_scratchpad(user_id)
        return Scratchpad.from_dict(d) if d else None

    def save_scratchpad(self, scratchpad: Scratchpad) -> None:
        self.db.save_scratchpad(scratchpad.to_dict())

    # ======================================================================
    # Conversation Turns
    # ======================================================================

    def save_turn(self, **kwargs: Any) -> None:
        self.db.save_turn(**kwargs)

    def get_unextracted_turns(self, limit: int = 100) -> list[dict]:
        return self.db.get_unextracted_turns(limit)

    def mark_turns_extracted(self, session_id: str, turn_indices: list[int]) -> None:
        self.db.mark_turns_extracted(session_id, turn_indices)

    def get_session_turns(self, session_id: str) -> list[dict]:
        return self.db.get_session_turns(session_id)

    def get_max_turn_index(self, session_id: str) -> int:
        return self.db.get_max_turn_index(session_id)

    def get_recent_turns(self, session_id: str, limit: int = 20) -> list[dict]:
        return self.db.get_recent_turns(session_id, limit)

    def get_global_recent_turns(self, limit: int = 20) -> list[dict]:
        return self.db.get_global_recent_turns(limit)

    def delete_turns_for_session(self, session_id: str) -> int:
        return self.db.delete_turns_for_session(session_id)

    def search_turns(self, keyword: str, **kwargs: Any) -> list[dict]:
        return self.db.search_turns(keyword, **kwargs)

    # ======================================================================
    # Extraction Queue
    # ======================================================================

    def enqueue_extraction(self, **kwargs: Any) -> None:
        self.db.enqueue_extraction(**kwargs)

    def dequeue_extraction(self, batch_size: int = 10) -> list[dict]:
        return self.db.dequeue_extraction(batch_size)

    def complete_extraction(self, queue_id: int, success: bool = True) -> None:
        self.db.complete_extraction(queue_id, success)

    # ======================================================================
    # Attachments (文件/媒体记忆)
    # ======================================================================

    def save_attachment(self, attachment: Attachment) -> str:
        self.db.save_attachment(attachment.to_dict())
        return attachment.id

    def get_attachment(self, attachment_id: str) -> Attachment | None:
        d = self.db.get_attachment(attachment_id)
        return Attachment.from_dict(d) if d else None

    def search_attachments(
        self,
        query: str = "",
        mime_type: str | None = None,
        direction: str | None = None,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[Attachment]:
        rows = self.db.search_attachments(
            query=query,
            mime_type=mime_type,
            direction=direction,
            session_id=session_id,
            limit=limit,
        )
        return [Attachment.from_dict(r) for r in rows]

    def delete_attachment(self, attachment_id: str) -> bool:
        return self.db.delete_attachment(attachment_id)

    def get_session_attachments(self, session_id: str) -> list[Attachment]:
        rows = self.db.get_session_attachments(session_id)
        return [Attachment.from_dict(r) for r in rows]

    # ======================================================================
    # Utilities
    # ======================================================================

    def get_stats(self, scope: str = "global", scope_owner: str = "") -> dict:
        return {
            "memory_count": self.db.count(scope=scope, scope_owner=scope_owner),
            "search_backend": self.search.backend_type,
            "search_available": self.search.available,
        }

    def close(self) -> None:
        if hasattr(self, "search") and hasattr(self.search, "close"):
            try:
                self.search.close()
            except Exception:
                pass
        self.db.close()
