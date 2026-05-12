"""
Comprehensive cancellation and concurrency control tests for TaskQueue.enqueue_task.
"""

import asyncio

import pytest

from openakita.agents.task_queue import TaskQueue


@pytest.fixture
def task_queue():
    return TaskQueue(max_concurrent=10, cleanup_interval=0)


# ═══════════════════════════════════════════════════════════════════
# Cancel queued tasks (not yet started)
# ═══════════════════════════════════════════════════════════════════

class TestCancelQueuedTasks:
    @pytest.mark.asyncio
    async def test_cancel_before_execution(self, task_queue):
        """Tasks enqueued but not yet started (waiting on semaphore) are cancelled."""
        executed = []

        async def _factory(n):
            executed.append(n)
            await asyncio.sleep(0.05)
            return n

        # Submit 5 tasks with max_concurrent=1 — only 1 runs, 4 queue
        futures = []
        for i in range(5):
            f = await task_queue.enqueue_task(
                factory=lambda n=i: _factory(n),
                org_id="org1",
                node_id="n1",
                max_concurrent=1,
            )
            futures.append(f)

        await asyncio.sleep(0.02)  # let first task start

        # Cancel all node tasks
        cancelled = await task_queue.cancel_node_tasks("org1", "n1")
        assert len(cancelled) >= 1  # at least the running task + pending futures

        # Only the first task should have executed (or maybe 0 if cancelled fast)
        assert len(executed) <= 1

        # Pending futures should be cancelled
        for i, f in enumerate(futures):
            if i > 0:
                assert f.cancelled() or f.done()

    @pytest.mark.asyncio
    async def test_cancel_only_specified_node(self, task_queue):
        """cancel_node_tasks only affects the specified node, not others."""
        executed_a = []
        executed_b = []

        async def _factory(n, store):
            store.append(n)
            await asyncio.sleep(0.05)
            return n

        await task_queue.enqueue_task(
            factory=lambda: _factory("a", executed_a),
            org_id="org1", node_id="n1", max_concurrent=1,
        )
        f_b = await task_queue.enqueue_task(
            factory=lambda: _factory("b", executed_b),
            org_id="org1", node_id="n2", max_concurrent=1,
        )

        await asyncio.sleep(0.01)
        await task_queue.cancel_node_tasks("org1", "n1")

        # Node n2 should still complete
        result = await asyncio.wait_for(f_b, timeout=2)
        assert result == "b"
        assert executed_b == ["b"]

    @pytest.mark.asyncio
    async def test_cancel_already_completed_noop(self, task_queue):
        """Cancelling already-completed tasks is a no-op (no exception)."""
        async def _factory():
            return "done"

        f = await task_queue.enqueue_task(
            factory=_factory, org_id="org1", node_id="n1", max_concurrent=1,
        )
        await asyncio.wait_for(f, timeout=2)
        assert await f == "done"

        # This should not raise
        cancelled = await task_queue.cancel_node_tasks("org1", "n1")
        assert len(cancelled) == 0  # Nothing to cancel


# ═══════════════════════════════════════════════════════════════════
# Per-node concurrency limits
# ═══════════════════════════════════════════════════════════════════

class TestPerNodeConcurrency:
    @pytest.mark.asyncio
    async def test_max_concurrent_enforced(self, task_queue):
        """Per-node semaphore limits concurrent executions."""
        running = 0
        max_seen = 0
        lock = asyncio.Lock()

        async def _factory(n):
            nonlocal running, max_seen
            async with lock:
                running += 1
                max_seen = max(max_seen, running)
            await asyncio.sleep(0.05)
            async with lock:
                running -= 1
            return n

        futures = []
        for i in range(5):
            f = await task_queue.enqueue_task(
                factory=lambda n=i: _factory(n),
                org_id="org1", node_id="n1", max_concurrent=2,
            )
            futures.append(f)

        for f in futures:
            await asyncio.wait_for(f, timeout=5)

        assert max_seen == 2  # Never exceeded max_concurrent

    @pytest.mark.asyncio
    async def test_independent_node_limits(self, task_queue):
        """Different nodes have independent concurrency limits."""
        lock = asyncio.Lock()

        async def _factory(n, active_ref, max_ref):
            async with lock:
                active_ref.append(1)
                max_ref.append(max(max_ref[-1:] + [len(active_ref)]))
            await asyncio.sleep(0.05)
            async with lock:
                active_ref.pop()
            return n

        a_active = []
        a_max = [0]
        b_active = []
        b_max = [0]

        futures = []
        for i in range(3):
            f = await task_queue.enqueue_task(
                factory=lambda n=i, aa=a_active, am=a_max: _factory(n, aa, am),
                org_id="org1", node_id="n1", max_concurrent=2,
            )
            futures.append(f)
        for i in range(3):
            f = await task_queue.enqueue_task(
                factory=lambda n=i, ba=b_active, bm=b_max: _factory(n, ba, bm),
                org_id="org1", node_id="n2", max_concurrent=1,
            )
            futures.append(f)

        for f in futures:
            await asyncio.wait_for(f, timeout=5)

        assert max(a_max) <= 2
        assert max(b_max) <= 1

    @pytest.mark.asyncio
    async def test_semaphore_released_after_completion(self, task_queue):
        """After a task completes, the next waiting task is immediately scheduled."""
        order = []
        lock = asyncio.Lock()

        async def _factory(n):
            async with lock:
                order.append(n)
            await asyncio.sleep(0.03)
            return n

        futures = []
        for i in range(3):
            f = await task_queue.enqueue_task(
                factory=lambda n=i: _factory(n),
                org_id="org1", node_id="n1", max_concurrent=1,
            )
            futures.append(f)

        for f in futures:
            await asyncio.wait_for(f, timeout=5)

        assert order == [0, 1, 2]


# ═══════════════════════════════════════════════════════════════════
# Org-level cancel all tasks
# ═══════════════════════════════════════════════════════════════════

class TestCancelOrgTasks:
    @pytest.mark.asyncio
    async def test_cancel_org_cancels_all_nodes(self, task_queue):
        """cancel_org_tasks cancels tasks from all nodes within the org."""
        executed = []

        async def _factory(name):
            executed.append(name)
            await asyncio.sleep(0.1)
            return name

        await task_queue.enqueue_task(
            factory=lambda: _factory("n1"), org_id="org1", node_id="n1", max_concurrent=1,
        )
        await task_queue.enqueue_task(
            factory=lambda: _factory("n2"), org_id="org1", node_id="n2", max_concurrent=1,
        )
        f3 = await task_queue.enqueue_task(
            factory=lambda: _factory("n3"), org_id="org2", node_id="n1", max_concurrent=1,
        )

        await asyncio.sleep(0.02)
        cancelled = await task_queue.cancel_org_tasks("org1")
        assert len(cancelled) >= 1

        # org2 task should still complete
        result = await asyncio.wait_for(f3, timeout=2)
        assert result == "n3"

    @pytest.mark.asyncio
    async def test_cancel_org_with_many_pending(self, task_queue):
        """Org cancel handles many pending futures without error."""
        futures = []
        for i in range(5):
            for node in ["n1", "n2"]:
                f = await task_queue.enqueue_task(
                    factory=lambda n=i: asyncio.sleep(0.1) or n,
                    org_id="org1", node_id=node, max_concurrent=1,
                )
                futures.append(f)

        await asyncio.sleep(0.01)
        await task_queue.cancel_org_tasks("org1")

        # All pending futures should be cancelled
        for f in futures:
            assert f.cancelled() or f.done()


# ═══════════════════════════════════════════════════════════════════
# Cancellation error handling
# ═══════════════════════════════════════════════════════════════════

class TestCancellationErrorHandling:
    @pytest.mark.asyncio
    async def test_factory_catches_cancelled_error(self, task_queue):
        """Factory receives CancelledError and can clean up."""
        cleaned_up = False
        blocker = asyncio.Event()

        async def _factory():
            nonlocal cleaned_up
            try:
                await blocker.wait()
            except asyncio.CancelledError:
                cleaned_up = True
                raise
            return "done"

        await task_queue.enqueue_task(
            factory=_factory, org_id="org1", node_id="n1", max_concurrent=1,
        )

        await asyncio.sleep(0.03)
        cancelled = await task_queue.cancel_node_tasks("org1", "n1")
        assert len(cancelled) >= 1

        # Await the cancelled tasks to let CancelledError propagate
        for t in cancelled:
            try:
                await t
            except asyncio.CancelledError:
                pass

        # Give the factory a chance to run its finally/except
        await asyncio.sleep(0)

        assert cleaned_up, "Factory should have caught CancelledError"

    @pytest.mark.asyncio
    async def test_cancel_unknown_node_noop(self, task_queue):
        """Cancelling a node with no tasks is a no-op."""
        cancelled = await task_queue.cancel_node_tasks("org99", "node99")
        assert cancelled == []

    @pytest.mark.asyncio
    async def test_enqueue_task_id_tracked(self, task_queue):
        """Task IDs are tracked in _registered and _index."""
        async def _factory():
            await asyncio.sleep(0.02)
            return "ok"

        f = await task_queue.enqueue_task(
            factory=_factory, org_id="org1", node_id="n1", max_concurrent=1,
        )
        await asyncio.wait_for(f, timeout=2)

        # After completion, deregistered
        assert len(task_queue._registered) == 0
        assert len(task_queue._node_futures) == 0
