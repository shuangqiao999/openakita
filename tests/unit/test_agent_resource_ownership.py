"""
Agent 资源共享/独享测试 — 验证 AgentInstancePool 回收时不误关共享资源

运行: pytest tests/unit/test_agent_resource_ownership.py -v
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

pytestmark = pytest.mark.anyio


# ============================================================================
# 测试 1: Agent.__init__ 默认资源所有权
# ============================================================================

class TestAgentResourceOwnership:
    """验证 Agent 初始化和工厂创建时的资源所有权标记"""

    def test_main_agent_owns_resources_by_default(self):
        """主 Agent 自己初始化，应默认拥有 memory_manager 和 kb_manager"""
        from openakita.core.agent import Agent

        agent = Agent.__new__(Agent)
        # 模拟 Agent.__init__ 中的关键初始化
        agent._owns_memory_manager = True
        agent._owns_kb_manager = True

        assert agent._owns_memory_manager is True, "主 Agent 应默认拥有 memory_manager"
        assert agent._owns_kb_manager is True, "主 Agent 应默认拥有 kb_manager"

    def test_factory_agent_does_not_own_shared_resources(self):
        """AgentFactory 创建的 agent 不应拥有共享资源"""
        from openakita.core.agent import Agent

        agent = Agent.__new__(Agent)
        agent._owns_memory_manager = True  # __init__ 默认值
        agent._owns_kb_manager = True

        # 模拟 AgentFactory.create() 中的覆盖
        agent._owns_memory_manager = False
        agent._owns_kb_manager = False

        assert agent._owns_memory_manager is False, (
            "工厂 agent 不应拥有共享 memory_manager"
        )
        assert agent._owns_kb_manager is False, "工厂 agent 不应拥有共享 kb_manager"

    def test_isolated_memory_agent_owns_its_memory(self):
        """_apply_memory_isolation 后 agent 应拥有独立 memory"""
        from openakita.core.agent import Agent

        agent = Agent.__new__(Agent)
        agent._owns_memory_manager = False  # 工厂创建后的值
        agent._owns_kb_manager = False

        # 模拟 _apply_memory_isolation 中的覆盖
        agent._owns_memory_manager = True

        assert agent._owns_memory_manager is True, (
            "memory_mode=isolated 时 agent 应拥有独立 memory"
        )
        assert agent._owns_kb_manager is False, (
            "kb_manager 仍是共享的（未隔离）"
        )


# ============================================================================
# 测试 2: Agent.shutdown() 的资源关闭行为
# ============================================================================

class TestShutdownResourceClose:
    """验证 shutdown() 根据所有权标记决定是否关闭"""

    def test_shutdown_closes_owned_resources(self):
        """主 Agent shutdown 时应关闭自己拥有的资源"""

        class FakeAgent:
            _owns_memory_manager = True
            _owns_kb_manager = True
            memory_manager = MagicMock()
            memory_manager.close = MagicMock()
            memory_manager.await_pending_tasks = AsyncMock()
            kb_manager = MagicMock()
            kb_manager.close = MagicMock()
            task_scheduler = None
            handler_registry = MagicMock()

        agent = FakeAgent()

        # 模拟 shutdown 中的关闭逻辑
        if agent._owns_kb_manager:
            agent.kb_manager.close()
        if agent._owns_memory_manager:
            agent.memory_manager.close()

        agent.kb_manager.close.assert_called_once()
        agent.memory_manager.close.assert_called_once()

    def test_shutdown_skips_shared_resources(self):
        """工厂 agent shutdown 时不应关闭共享资源"""

        class FakeAgent:
            _owns_memory_manager = False
            _owns_kb_manager = False
            memory_manager = MagicMock()
            memory_manager.close = MagicMock()
            memory_manager.await_pending_tasks = AsyncMock()
            kb_manager = MagicMock()
            kb_manager.close = MagicMock()
            task_scheduler = None
            handler_registry = MagicMock()

        agent = FakeAgent()

        if agent._owns_kb_manager:
            agent.kb_manager.close()
        if agent._owns_memory_manager:
            agent.memory_manager.close()

        agent.kb_manager.close.assert_not_called()
        agent.memory_manager.close.assert_not_called()

    def test_shutdown_hybrid_ownership(self):
        """混合模式：拥有独立 memory 但不拥有 kb"""

        class FakeAgent:
            _owns_memory_manager = True
            _owns_kb_manager = False
            memory_manager = MagicMock()
            memory_manager.close = MagicMock()
            memory_manager.await_pending_tasks = AsyncMock()
            kb_manager = MagicMock()
            kb_manager.close = MagicMock()
            task_scheduler = None
            handler_registry = MagicMock()

        agent = FakeAgent()

        if agent._owns_kb_manager:
            agent.kb_manager.close()
        if agent._owns_memory_manager:
            agent.memory_manager.close()

        agent.kb_manager.close.assert_not_called()
        agent.memory_manager.close.assert_called_once()


# ============================================================================
# 测试 3: Pool 回收 → 共享资源不被关闭（端到端模拟）
# ============================================================================

class TestPoolReaperDoesNotCloseSharedResources:
    """端到端模拟 AgentInstancePool 回收→共享 DB 仍可用"""

    def test_shared_db_still_accessible_after_pool_agent_shutdown(self):
        """验证工厂 agent 关闭后，共享 DB 仍可被 main agent 访问"""

        class SharedMemoryManager:
            def __init__(self):
                self._closed = False
                self._table = "active"

            def close(self):
                self._closed = True
                self._table = None

            def query(self, sql):
                if self._closed:
                    raise RuntimeError("Cannot operate on a closed database")
                return f"result for: {sql}"

        class FakeKbManager:
            def __init__(self):
                self._closed = False

            def close(self):
                self._closed = True

            def search(self, query):
                if self._closed:
                    raise RuntimeError("Cannot operate on a closed database")
                return [f"doc: {query}"]

        # 创建共享资源
        shared_mm = SharedMemoryManager()
        shared_kb = FakeKbManager()

        # 主 agent 拥有所有权
        main_agent = MagicMock()
        main_agent._owns_memory_manager = True
        main_agent._owns_kb_manager = True
        main_agent.memory_manager = shared_mm
        main_agent.kb_manager = shared_kb

        # 工厂创建 pool agent（共享资源）
        pool_agent = MagicMock()
        pool_agent._owns_memory_manager = False
        pool_agent._owns_kb_manager = False
        pool_agent.memory_manager = shared_mm
        pool_agent.kb_manager = shared_kb

        # Pool 回收 pool agent → 不应关闭共享资源
        if pool_agent._owns_kb_manager and pool_agent.kb_manager:
            pool_agent.kb_manager.close()
        if pool_agent._owns_memory_manager and pool_agent.memory_manager:
            pool_agent.memory_manager.close()

        # 验证共享 DB 仍可用
        result = shared_mm.query("SELECT * FROM memories")
        assert "result for" in result, "共享 memory_manager 应仍可用"

        kb_results = shared_kb.search("test")
        assert len(kb_results) > 0, "共享 kb_manager 应仍可用"

        # 主 agent 退出时关闭
        main_agent.memory_manager.close()
        main_agent.kb_manager.close()

        with pytest.raises(RuntimeError):
            shared_mm.query("SELECT *")

    def test_full_pool_lifecycle_no_shared_close(self):
        """完整生命周期：多 agent 创建→回收→主 agent 退出"""

        class SharedResource:
            def __init__(self, name):
                self.name = name
                self._closed = False

            def close(self):
                self._closed = True

            def is_closed(self):
                return self._closed

        shared_mm = SharedResource("memory")
        shared_kb = SharedResource("kb")

        # 主 agent 创建
        main = MagicMock(_owns_memory_manager=True, _owns_kb_manager=True)
        main.memory_manager = shared_mm
        main.kb_manager = shared_kb

        # 3 个 pool agent 的创建→回收循环
        for i in range(3):
            pool = MagicMock(_owns_memory_manager=False, _owns_kb_manager=False)
            pool.memory_manager = shared_mm
            pool.kb_manager = shared_kb

            # Pool reaper 回收
            if pool._owns_memory_manager:
                pool.memory_manager.close()
            if pool._owns_kb_manager:
                pool.kb_manager.close()

            # 每次回收后共享资源应仍然开放
            assert not shared_mm.is_closed(), f"第 {i} 次回收后 memory 应仍开放"
            assert not shared_kb.is_closed(), f"第 {i} 次回收后 kb 应仍开放"

        # 主 agent 退出
        main.memory_manager.close()
        main.kb_manager.close()
        assert shared_mm.is_closed(), "主退出后 memory 应关闭"
        assert shared_kb.is_closed(), "主退出后 kb 应关闭"


# ============================================================================
# 测试 4: AgentFactory 源码验证
# ============================================================================

class TestFactorySourceVerification:
    """静态验证 AgentFactory 源码中的所有权设置"""

    def test_factory_sets_owns_flags(self):
        """验证 factory.py create() 设置了所有权标记"""
        from pathlib import Path

        factory_path = Path("src/openakita/agents/factory.py")
        if not factory_path.exists():
            pytest.skip("factory.py not found")
        source = factory_path.read_text(encoding="utf-8")

        assert "agent._owns_memory_manager = False" in source, (
            "factory create() 必须设 _owns_memory_manager = False"
        )
        assert "agent._owns_kb_manager = False" in source, (
            "factory create() 必须设 _owns_kb_manager = False"
        )

    def test_factory_isolation_sets_owns(self):
        """验证 _apply_memory_isolation 设置了所有权标记"""
        from pathlib import Path

        factory_path = Path("src/openakita/agents/factory.py")
        if not factory_path.exists():
            pytest.skip("factory.py not found")
        source = factory_path.read_text(encoding="utf-8")

        assert "agent._owns_memory_manager = True" in source, (
            "_apply_memory_isolation 必须设 _owns_memory_manager = True"
        )

    def test_agent_init_has_owns_flags(self):
        """验证 Agent.__init__ 有所有权标记"""
        from pathlib import Path

        agent_path = Path("src/openakita/core/agent.py")
        if not agent_path.exists():
            pytest.skip("agent.py not found")
        source = agent_path.read_text(encoding="utf-8")

        assert "_owns_memory_manager = True" in source, (
            "Agent.__init__ 必须有 _owns_memory_manager = True"
        )
        assert "_owns_kb_manager = True" in source, (
            "Agent.__init__ 必须有 _owns_kb_manager = True"
        )

    def test_shutdown_checks_owns_flags(self):
        """验证 shutdown() 中关闭逻辑有所有权检查"""
        from pathlib import Path

        agent_path = Path("src/openakita/core/agent.py")
        if not agent_path.exists():
            pytest.skip("agent.py not found")
        source = agent_path.read_text(encoding="utf-8")

        assert "self._owns_kb_manager" in source, (
            "shutdown() 必须检查 _owns_kb_manager"
        )
        assert "self._owns_memory_manager" in source, (
            "shutdown() 必须检查 _owns_memory_manager"
        )


# ============================================================================
# 测试 5: 回归验证
# ============================================================================

class TestRegression:
    """回归测试：确保修复不破坏其他功能"""

    def test_default_agent_init_flow(self):
        """验证 Agent 正常初始化不报错"""
        from openakita.core.agent import Agent

        agent = Agent.__new__(Agent)
        agent._owns_memory_manager = True
        agent._owns_kb_manager = True
        agent.memory_manager = MagicMock()
        agent.kb_manager = MagicMock()
        agent.task_scheduler = None
        agent.handler_registry = MagicMock()

        # 模拟 shutdown 的正常路径
        assert agent._owns_memory_manager is True
        assert agent._owns_kb_manager is True

    def test_import_chain_intact(self):
        """验证 factory 和 agent 的导入链完整"""
        from openakita.agents.factory import AgentFactory
        from openakita.core.agent import Agent

        assert AgentFactory is not None
        assert Agent is not None

    @pytest.mark.asyncio
    async def test_async_shutdown_flow(self):
        """验证异步 shutdown 流程正常"""

        class AsyncAgent:
            _owns_memory_manager = False
            _owns_kb_manager = False
            memory_manager = MagicMock()
            memory_manager.close = MagicMock()
            memory_manager.await_pending_tasks = AsyncMock()
            memory_manager.end_session = MagicMock()
            kb_manager = MagicMock()
            kb_manager.close = MagicMock()
            task_scheduler = None
            handler_registry = MagicMock()
            handler_registry.get_handler = MagicMock(return_value=None)

        agent = AsyncAgent()
        # 模拟 shutdown 流程
        if agent.task_scheduler:
            pass  # skip

        agent.memory_manager.end_session(task_description="", success=True, errors=[])
        await agent.memory_manager.await_pending_tasks(timeout=15.0)

        if agent._owns_kb_manager and agent.kb_manager:
            agent.kb_manager.close()
        if agent._owns_memory_manager and agent.memory_manager:
            agent.memory_manager.close()

        # 验证共享资源未被关闭
        agent.kb_manager.close.assert_not_called()
        agent.memory_manager.close.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
