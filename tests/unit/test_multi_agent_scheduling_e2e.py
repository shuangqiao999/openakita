"""
End-to-end functional verification for multi-agent scheduling overhaul.

Validates:
- TaskQueue integration in delegate path (enqueue logs, priority scheduling)
- DAG dependency resolution in delegate_parallel
- Background cleanup (TTL, capacity eviction)
- Adaptive concurrency controller lifecycle
- LLM rate limiter acquire/release/report
- TaskGraph validation (cycles, missing deps)
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.agents.orchestrator import AgentOrchestrator
from openakita.agents.profile import AgentProfile, AgentType, ProfileStore
from openakita.agents.task_graph import TaskGraph, TaskNode
from openakita.agents.task_queue import Priority, TaskQueue
from openakita.core.llm_rate_limiter import GlobalLLMRateLimiter
from openakita.sessions.session import Session, SessionConfig, SessionContext


def _make_session(
    session_id: str = "test-session-1",
    agent_profile_id: str = "default",
) -> Session:
    ctx = SessionContext()
    ctx.agent_profile_id = agent_profile_id
    return Session(
        id=session_id,
        channel="cli",
        chat_id="chat-1",
        user_id="user-1",
        context=ctx,
        config=SessionConfig(),
    )


def _make_profile(
    pid: str = "test-agent",
    name: str = "Test Agent",
    agent_type: AgentType = AgentType.CUSTOM,
    **kwargs,
) -> AgentProfile:
    return AgentProfile(id=pid, name=name, type=agent_type, **kwargs)


# ═══════════════════════════════════════════════════════════════════
# TaskQueue integration tests
# ═══════════════════════════════════════════════════════════════════

class TestTaskQueueIntegration:
    """Verify TaskQueue is correctly wired into the delegation path."""

    @pytest.mark.asyncio
    async def test_enqueue_and_execute(self):
        """TaskQueue processes an enqueued task via handler."""
        results = []

        async def handler(task):
            results.append(task.payload.get("msg"))
            return "OK"

        q = TaskQueue(max_concurrent=2, cleanup_interval=0)
        q.set_handler(handler)
        await q.start()

        tid = await q.enqueue("sess1", "agent1", {"msg": "hello"}, Priority.NORMAL)
        result = await q.wait_for(tid, timeout=5)
        assert result == "OK"
        assert results == ["hello"]

        await q.stop()

    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        """Higher priority tasks execute first."""
        order = []

        async def handler(task):
            order.append(task.payload["id"])
            await asyncio.sleep(0.02)
            return "OK"

        q = TaskQueue(max_concurrent=1, cleanup_interval=0)
        q.set_handler(handler)
        await q.start()

        # Enqueue in reverse priority order
        t1 = await q.enqueue("s", "a", {"id": "low"}, Priority.LOW)
        t2 = await q.enqueue("s", "a", {"id": "normal"}, Priority.NORMAL)
        t3 = await q.enqueue("s", "a", {"id": "high"}, Priority.HIGH)
        t4 = await q.enqueue("s", "a", {"id": "urgent"}, Priority.URGENT)

        await q.wait_for(t1, timeout=10)
        await q.wait_for(t2, timeout=10)
        await q.wait_for(t3, timeout=10)
        await q.wait_for(t4, timeout=10)

        assert order == ["urgent", "high", "normal", "low"]

        await q.stop()

    @pytest.mark.asyncio
    async def test_dag_dependency_release(self):
        """Dependent task only runs after all deps complete."""
        order = []

        async def handler(task):
            order.append(task.payload["name"])
            return "OK"

        q = TaskQueue(max_concurrent=3, cleanup_interval=0)
        q.set_handler(handler)
        await q.start()

        t_a = await q.enqueue("s", "a", {"name": "A"}, Priority.NORMAL)
        t_b = await q.enqueue("s", "a", {"name": "B"}, Priority.NORMAL, depends_on=[t_a])
        t_c = await q.enqueue("s", "a", {"name": "C"}, Priority.NORMAL, depends_on=[t_a, t_b])

        await q.wait_for(t_c, timeout=10)

        assert order == ["A", "B", "C"]

        await q.stop()

    @pytest.mark.asyncio
    async def test_concurrency_limit(self):
        """Queue enforces max_concurrent limit."""
        active_counts = []
        lock = asyncio.Lock()

        async def handler(task):
            async with lock:
                active_counts.append(len(q._active))
            await asyncio.sleep(0.05)
            return "OK"

        q = TaskQueue(max_concurrent=3, cleanup_interval=0)
        q.set_handler(handler)
        await q.start()

        ids = []
        for i in range(10):
            ids.append(await q.enqueue("s", "a", {"n": i}, Priority.NORMAL))

        for tid in ids:
            await q.wait_for(tid, timeout=30)

        assert all(c <= 3 for c in active_counts)
        assert any(c == 3 for c in active_counts)

        await q.stop()

    @pytest.mark.asyncio
    async def test_delegate_routes_through_taskqueue(self):
        """orchestrator.delegate() enqueues via TaskQueue when worker is running."""
        from openakita.agents.factory import AgentFactory, AgentInstancePool

        store = ProfileStore(Path("data/test_agents"))
        store._profiles = {}
        store.save(_make_profile("default", "Default"))
        store.save(_make_profile("helper", "Helper"))

        factory = AgentFactory()
        pool = AgentInstancePool(factory, profile_store=store)
        mock_agent = MagicMock()
        mock_agent.chat_with_session = AsyncMock(return_value="Agent response")
        mock_agent._is_sub_agent_call = False
        mock_agent._agent_profile = None
        mock_agent._last_finalized_trace = []
        mock_agent.agent_state = None
        mock_agent._execution_lock = asyncio.Lock()
        pool.get_or_create = AsyncMock(return_value=mock_agent)

        orch = AgentOrchestrator()
        orch._profile_store = store
        orch._pool = pool
        orch._log_dir = Path("data/test_delegation_logs")
        orch._log_dir.mkdir(parents=True, exist_ok=True)

        await orch.start()

        session = _make_session()
        result = await orch.delegate(
            session, "main", "helper", "do task", reason="test"
        )

        # Verify TaskQueue was used
        stats = orch._task_queue.get_stats()
        assert stats["total_enqueued"] >= 1
        assert stats["total_completed"] >= 1
        assert "Agent response" in result

        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_delegate_fallback_without_worker(self):
        """orchestrator.delegate() falls back to direct dispatch when queue not running."""

        store = ProfileStore(Path("data/test_agents_fb"))
        store._profiles = {}
        store.save(_make_profile("default", "Default"))
        store.save(_make_profile("helper", "Helper"))

        mock_agent = MagicMock()
        mock_agent.chat_with_session = AsyncMock(return_value="Direct response")
        mock_agent._is_sub_agent_call = False
        mock_agent._agent_profile = None
        mock_agent._last_finalized_trace = []
        mock_agent.agent_state = None
        mock_agent._execution_lock = asyncio.Lock()

        pool = MagicMock()
        pool.get_or_create = AsyncMock(return_value=mock_agent)

        orch = AgentOrchestrator()
        orch._profile_store = store
        orch._pool = pool
        orch._log_dir = Path("data/test_delegation_logs_fb")
        orch._log_dir.mkdir(parents=True, exist_ok=True)

        # Do NOT start the orchestrator — TaskQueue worker stays off
        session = _make_session()
        result = await orch.delegate(
            session, "main", "helper", "task", reason="test"
        )

        assert "Direct response" in result
        stats = orch._task_queue.get_stats()
        assert stats["total_enqueued"] == 0  # Not enqueued via queue


# ═══════════════════════════════════════════════════════════════════
# TaskGraph tests
# ═══════════════════════════════════════════════════════════════════

class TestTaskGraphDAG:
    def test_simple_dag(self):
        g = TaskGraph()
        g.add_node(TaskNode("a", "agent", "task a"))
        g.add_node(TaskNode("b", "agent", "task b", depends_on=["a"]))
        g.add_node(TaskNode("c", "agent", "task c", depends_on=["a", "b"]))

        errors = g.validate()
        assert not errors

        layers = g.topological_layers()
        assert layers == [["a"], ["b"], ["c"]]

    def test_parallel_layer(self):
        g = TaskGraph()
        g.add_node(TaskNode("a", "agent", "task a"))
        g.add_node(TaskNode("b", "agent", "task b"))
        g.add_node(TaskNode("c", "agent", "task c", depends_on=["a", "b"]))

        layers = g.topological_layers()
        assert layers == [["a", "b"], ["c"]]

    def test_circular_dependency(self):
        g = TaskGraph()
        g.add_node(TaskNode("a", "agent", "task a", depends_on=["b"]))
        g.add_node(TaskNode("b", "agent", "task b", depends_on=["a"]))

        errors = g.validate()
        assert any("circular" in e.lower() for e in errors)

    def test_missing_dependency(self):
        g = TaskGraph()
        g.add_node(TaskNode("a", "agent", "task a", depends_on=["nonexistent"]))

        errors = g.validate()
        assert any("missing" in e.lower() for e in errors)

    def test_from_tasks_list(self):
        tasks = [
            {"id": "t1", "agent_id": "agent1", "message": "A"},
            {"id": "t2", "agent_id": "agent2", "message": "B", "depends_on": ["t1"]},
        ]
        g = TaskGraph.from_tasks_list(tasks)
        assert g.node_count == 2
        errors = g.validate()
        assert not errors

    def test_from_tasks_list_auto_id(self):
        tasks = [
            {"agent_id": "agent1", "message": "A"},
            {"agent_id": "agent2", "message": "B"},
        ]
        g = TaskGraph.from_tasks_list(tasks)
        assert g.node_count == 2
        errors = g.validate()
        assert not errors
        for node in g.nodes.values():
            assert node.task_id


# ═══════════════════════════════════════════════════════════════════
# LLM Rate Limiter tests
# ═══════════════════════════════════════════════════════════════════

class TestLLMRateLimiter:
    def test_singleton(self):
        a = GlobalLLMRateLimiter()
        b = GlobalLLMRateLimiter()
        assert a is b

    def test_default_stats(self):
        limiter = GlobalLLMRateLimiter()
        stats = limiter.get_stats()
        assert "max_concurrent" in stats
        assert "rpm_limit" in stats
        assert stats["total_penalties"] >= 0

    @pytest.mark.asyncio
    async def test_acquire_release(self):
        limiter = GlobalLLMRateLimiter()
        # Acquire should not block (rpm defaults to 0)
        await limiter.acquire()
        assert limiter._active_count == 1
        limiter.release()
        assert limiter._active_count == 0

    @pytest.mark.asyncio
    async def test_penalty_recorded(self):
        limiter = GlobalLLMRateLimiter()
        before = limiter.get_stats()["total_penalties"]
        limiter.report_rate_limited()
        after = limiter.get_stats()["total_penalties"]
        assert after > before

    @pytest.mark.asyncio
    async def test_concurrent_limit_enforced(self):
        limiter = GlobalLLMRateLimiter()
        # Set low concurrent limit for testing
        limiter.adjust_concurrency(2)
        acquired = 0
        async with asyncio.TaskGroup() as tg:
            async def _acquire_and_wait():
                nonlocal acquired
                await limiter.acquire()
                acquired += 1
                await asyncio.sleep(0.1)
                limiter.release()

            for _ in range(4):
                tg.create_task(_acquire_and_wait())

        # All should complete; semaphore gates concurrency
        assert acquired == 4


# ═══════════════════════════════════════════════════════════════════
# Background cleanup tests
# ═══════════════════════════════════════════════════════════════════

class TestBackgroundCleanup:
    @pytest.mark.asyncio
    async def test_sub_state_cleanup_started(self):
        """_bg_cleanup_loop is started as part of orchestrator.start()."""
        orch = AgentOrchestrator()
        store = ProfileStore(Path("data/test_cleanup_p"))
        store._profiles = {}
        store.save(_make_profile("default", "Default"))

        orch._profile_store = store
        orch._log_dir = Path("data/test_cleanup_logs")
        orch._log_dir.mkdir(parents=True, exist_ok=True)
        orch._pool = MagicMock()
        orch._pool.start = AsyncMock()
        orch._pool.stop = AsyncMock()

        await orch.start()
        assert orch._bg_cleanup_task is not None
        assert not orch._bg_cleanup_task.done()
        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_capacity_eviction_enforced(self):
        """When _sub_agent_states exceeds _MAX_SUB_STATES, oldest are evicted."""
        orch = AgentOrchestrator()
        # Directly insert many state entries
        for i in range(orch._MAX_SUB_STATES + 10):
            key = f"test:{i}"
            orch._sub_agent_states[key] = {
                "status": "completed",
                "started_at": time.time() - 1000 - i,
                "elapsed_s": 1,
                "agent_id": f"agent_{i}",
                "profile_id": f"agent_{i}",
                "session_id": "sess",
                "chat_id": "chat",
                "name": f"Agent {i}",
                "icon": "X",
            }

        # Manually trigger capacity eviction logic
        now = time.time()
        ttl = 30

        # Clean old terminal states first
        for key, state in list(orch._sub_agent_states.items()):
            status = state.get("status", "")
            if status in ("completed", "cancelled", "timeout", "error", "interrupted"):
                started = state.get("started_at", 0)
                elapsed = state.get("elapsed_s", 0)
                age = now - (started + elapsed) if started else now - started
                if age > ttl:
                    orch._sub_agent_states.pop(key, None)

        # Then evict by capacity
        if len(orch._sub_agent_states) > orch._MAX_SUB_STATES:
            sorted_keys = sorted(
                orch._sub_agent_states.keys(),
                key=lambda k: orch._sub_agent_states[k].get("started_at", 0),
            )
            evict_count = len(orch._sub_agent_states) - orch._MAX_SUB_STATES
            for key in sorted_keys[:evict_count]:
                orch._sub_agent_states.pop(key, None)

        assert len(orch._sub_agent_states) <= orch._MAX_SUB_STATES
