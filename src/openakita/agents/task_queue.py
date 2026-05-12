"""
Priority TaskQueue for multi-agent task management.

Supports:
- Priority-based scheduling (URGENT > HIGH > NORMAL > LOW > BACKGROUND)
- DAG dependency resolution via TaskGraph
- Work-stealing: idle slots can pull from other queues
- Cancellation support
- Auto-cleanup of stale entries
"""

import asyncio
import heapq
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    URGENT = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


@dataclass(order=True)
class QueuedTask:
    priority: int
    created_at: float = field(compare=True)
    task_id: str = field(default_factory=lambda: f"qt_{uuid.uuid4().hex[:10]}", compare=False)
    agent_profile_id: str = field(default="default", compare=False)
    session_key: str = field(default="", compare=False)
    payload: dict = field(default_factory=dict, compare=False)
    depends_on: list[str] = field(default_factory=list, compare=False)
    cancelled: bool = field(default=False, compare=False)


class TaskQueue:
    """Async priority task queue with DAG support, work-stealing, and metrics.

    Usage:
        queue = TaskQueue(max_concurrent=3, cleanup_interval=60)
        await queue.start()
        tid = await queue.enqueue("sess", "agent", payload, Priority.NORMAL, depends_on=[])
        result = await queue.wait_for(tid)
        await queue.stop()
    """

    def __init__(
        self,
        max_concurrent: int = 5,
        cleanup_interval: int = 60,
        enable_work_stealing: bool = False,
    ):
        self._heap: list[QueuedTask] = []
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Event()
        self._results: dict[str, asyncio.Future] = {}
        self._active: dict[str, asyncio.Task] = {}
        self._max_concurrent = max_concurrent
        self._handler: Callable[[QueuedTask], Awaitable[Any]] | None = None
        self._running = False
        self._worker_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._total_enqueued = 0
        self._total_completed = 0
        self._total_failed = 0
        self._total_cancelled = 0
        self._total_stolen = 0

        self._completed_ids: set[str] = set()
        self._dependency_map: dict[str, list[str]] = {}
        self._deferred_tasks: dict[str, QueuedTask] = {}

        self._cleanup_interval = cleanup_interval

        self._steal_from: list[TaskQueue] = []
        self._enable_work_stealing = enable_work_stealing

        # ── Org / Node tracking (for orgs.runtime migration) ──
        # Externally-registered asyncio.Task objects with org/node metadata.
        self._registered: dict[str, asyncio.Task] = {}       # task_id -> Task
        self._meta: dict[str, tuple[str, str]] = {}          # task_id -> (org_id, node_id)
        self._index: dict[tuple[str, str], set[str]] = {}    # (org_id,node_id) -> {task_ids}
        self._track_lock = asyncio.Lock()

    def set_handler(self, handler: Callable[[QueuedTask], Awaitable[Any]]) -> None:
        self._handler = handler

    def add_steal_target(self, other: "TaskQueue") -> None:
        if other is not self and other not in self._steal_from:
            self._steal_from.append(other)

    # ── lifecycle ─────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop(), name="taskqueue_worker")
        if self._cleanup_interval > 0:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="taskqueue_cleanup")
        logger.info("[TaskQueue] Started (max_concurrent=%d)", self._max_concurrent)

    async def stop(self) -> None:
        self._running = False
        self._not_empty.set()
        for task in [self._worker_task, self._cleanup_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        for _tid, t in self._active.items():
            if not t.done():
                t.cancel()
        self._active.clear()
        for qt in self._heap:
            fut = self._results.pop(qt.task_id, None)
            if fut and not fut.done():
                fut.cancel()
        self._heap.clear()
        for _tid, fut in list(self._results.items()):
            if not fut.done():
                fut.cancel()
        self._results.clear()
        self._completed_ids.clear()
        self._dependency_map.clear()
        self._deferred_tasks.clear()
        self._registered.clear()
        self._meta.clear()
        self._index.clear()
        logger.info("[TaskQueue] Stopped")

    # ── enqueue ───────────────────────────────────────────────────

    async def enqueue(
        self,
        session_key: str,
        agent_profile_id: str,
        payload: dict,
        priority: Priority = Priority.NORMAL,
        depends_on: list[str] | None = None,
    ) -> str:
        """Add a task. Returns task_id. Tasks with unmet deps stay pending."""
        task = QueuedTask(
            priority=priority.value,
            created_at=time.time(),
            agent_profile_id=agent_profile_id,
            session_key=session_key,
            payload=payload,
            depends_on=list(depends_on or []),
        )
        async with self._lock:
            self._results[task.task_id] = asyncio.get_running_loop().create_future()
            self._total_enqueued += 1
            if task.depends_on:
                self._dependency_map[task.task_id] = list(task.depends_on)
                if not all(dep in self._completed_ids for dep in task.depends_on):
                    self._deferred_tasks[task.task_id] = task
                    logger.debug(
                        "[TaskQueue] Task %s waiting for deps: %s",
                        task.task_id[:8],
                        task.depends_on,
                    )
                    return task.task_id
            heapq.heappush(self._heap, task)
        self._not_empty.set()
        logger.debug("[TaskQueue] Enqueued %s (priority=%s)", task.task_id[:8], priority.name)
        return task.task_id

    def _release_dependants(self, completed_id: str) -> None:
        newly_ready: list[QueuedTask] = []
        for tid, deps in list(self._dependency_map.items()):
            if completed_id in deps:
                deps.remove(completed_id)
                if not deps:
                    self._dependency_map.pop(tid, None)
                    deferred = self._deferred_tasks.pop(tid, None)
                    if deferred is not None:
                        heapq.heappush(self._heap, deferred)
                        newly_ready.append(deferred)
                    else:
                        tsk = QueuedTask(
                            priority=Priority.NORMAL.value,
                            created_at=time.time(),
                            task_id=tid,
                            session_key="",
                            payload={},
                        )
                        self._results[tid] = self._results.get(tid) or asyncio.get_running_loop().create_future()
                        heapq.heappush(self._heap, tsk)
        if newly_ready:
            self._not_empty.set()
            logger.debug(
                "[TaskQueue] Released %d dependant(s) after %s",
                len(newly_ready),
                completed_id[:8],
            )

    async def cancel(self, task_id: str) -> bool:
        async with self._lock:
            for t in self._heap:
                if t.task_id == task_id and not t.cancelled:
                    t.cancelled = True
                    self._total_cancelled += 1
                    fut = self._results.get(task_id)
                    if fut and not fut.done():
                        fut.cancel()
                    return True
        active = self._active.get(task_id)
        if active and not active.done():
            active.cancel()
            self._total_cancelled += 1
            return True
        return False

    async def wait_for(self, task_id: str, timeout: float = 120.0) -> Any:
        fut = self._results.get(task_id)
        if fut is None:
            raise KeyError(f"Unknown task: {task_id}")
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._results.pop(task_id, None)

    # ── work-stealing ─────────────────────────────────────────────

    async def steal_task(self) -> QueuedTask | None:
        if not self._enable_work_stealing or not self._steal_from:
            return None
        for other in self._steal_from:
            async with other._lock:
                if other._heap:
                    task = other._heap.pop()
                    heapq.heapify(other._heap)
                    self._total_stolen += 1
                    logger.debug(
                        "[TaskQueue] Stolen task %s from queue %s",
                        task.task_id[:8],
                        id(other),
                    )
                    return task
        return None

    # ── worker + cleanup ──────────────────────────────────────────

    async def _worker_loop(self) -> None:
        while self._running:
            async with self._lock:
                task = heapq.heappop(self._heap) if self._heap else None

            if task is None:
                if self._enable_work_stealing:
                    stolen = await self.steal_task()
                    if stolen:
                        task = stolen
                if task is None:
                    self._not_empty.clear()
                    await self._not_empty.wait()
                    if not self._running:
                        break
                    continue

            if task.cancelled:
                self._results.pop(task.task_id, None)
                continue

            while len(self._active) >= self._max_concurrent and self._active:
                tasks = list(self._active.values())
                if not tasks:
                    break
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finished_ids = [tid for tid, t in self._active.items() if t.done()]
                for tid in finished_ids:
                    self._active.pop(tid, None)

            self._active[task.task_id] = asyncio.create_task(
                self._execute_task(task), name=f"qt_{task.task_id[:8]}"
            )

    async def _execute_task(self, task: QueuedTask) -> None:
        fut = self._results.get(task.task_id)
        try:
            if self._handler is None:
                raise RuntimeError("No handler set for TaskQueue")
            result = await self._handler(task)
            if fut and not fut.done():
                fut.set_result(result)
            self._total_completed += 1
            async with self._lock:
                self._completed_ids.add(task.task_id)
            self._release_dependants(task.task_id)
        except asyncio.CancelledError:
            if fut and not fut.done():
                fut.cancel()
            self._total_cancelled += 1
        except Exception as e:
            if fut and not fut.done():
                fut.set_exception(e)
            self._total_failed += 1
            logger.error("[TaskQueue] Task %s failed: %s", task.task_id[:8], e)

    async def _cleanup_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._purge_stale_futures()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("[TaskQueue] Cleanup error: %s", e)

    async def _purge_stale_futures(self) -> None:
        async with self._lock:
            stale_futs = [
                tid
                for tid, fut in self._results.items()
                if fut.done() and tid not in self._active
            ]
            for tid in stale_futs:
                self._results.pop(tid, None)
            stale_deps = [
                tid for tid in self._dependency_map if tid not in self._results
            ]
            for tid in stale_deps:
                self._dependency_map.pop(tid, None)
            self._prune_registered()

    # ── Org / Node task tracking ──────────────────────────────────

    def register_task(
        self,
        task_id: str,
        org_id: str,
        node_id: str,
        task: asyncio.Task,
    ) -> None:
        """Register an externally-created asyncio.Task with org/node metadata.

        This is a synchronous method (no await) safe for use in done-callbacks.
        """
        self._registered[task_id] = task
        self._meta[task_id] = (org_id, node_id)
        key = (org_id, node_id)
        self._index.setdefault(key, set()).add(task_id)

    def deregister_task(self, task_id: str) -> None:
        """Remove a task from org/node tracking (called from done-callback)."""
        self._registered.pop(task_id, None)
        meta = self._meta.pop(task_id, None)
        if meta is not None:
            key = (meta[0], meta[1])
            ids = self._index.get(key)
            if ids is not None:
                ids.discard(task_id)
                if not ids:
                    self._index.pop(key, None)

    async def get_node_active_count(self, org_id: str, node_id: str) -> int:
        """Count non-done registered tasks for an org+node."""
        key = (org_id, node_id)
        ids = self._index.get(key, set())
        count = 0
        for tid in list(ids):
            t = self._registered.get(tid)
            if t is not None and not t.done():
                count += 1
        return count

    def get_node_task_ids(self, org_id: str, node_id: str) -> list[str]:
        """Get task_ids of registered tasks for a node (used by watchdog)."""
        key = (org_id, node_id)
        return list(self._index.get(key, set()))

    async def cancel_node_tasks(self, org_id: str, node_id: str) -> list[asyncio.Task]:
        """Cancel all active registered tasks for a node. Returns cancelled tasks."""
        key = (org_id, node_id)
        ids = list(self._index.get(key, set()))
        cancelled: list[asyncio.Task] = []
        for tid in ids:
            t = self._registered.get(tid)
            if t is not None and not t.done():
                t.cancel()
                cancelled.append(t)
        return cancelled

    async def cancel_org_tasks(self, org_id: str) -> list[asyncio.Task]:
        """Cancel all active registered tasks for an org. Returns cancelled tasks."""
        cancelled: list[asyncio.Task] = []
        for (o_id, _n_id), ids in list(self._index.items()):
            if o_id != org_id:
                continue
            for tid in list(ids):
                t = self._registered.get(tid)
                if t is not None and not t.done():
                    t.cancel()
                    cancelled.append(t)
        return cancelled

    def _prune_registered(self) -> None:
        """Remove done/finished tasks from registered tracking."""
        done_ids = [tid for tid, t in self._registered.items() if t.done()]
        for tid in done_ids:
            self.deregister_task(tid)

    def get_stats(self) -> dict:
        return {
            "pending": len(self._heap),
            "blocked_by_deps": len(self._dependency_map),
            "active": len(self._active),
            "total_enqueued": self._total_enqueued,
            "total_completed": self._total_completed,
            "total_failed": self._total_failed,
            "total_cancelled": self._total_cancelled,
            "total_stolen": self._total_stolen,
            "max_concurrent": self._max_concurrent,
            "registered": len(self._registered),
        }
