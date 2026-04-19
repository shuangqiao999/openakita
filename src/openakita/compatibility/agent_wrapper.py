"""
Agent 包装器 - 向后兼容层

将旧的 Agent API 包装到新的作战指挥室架构上，
确保现有代码无需修改即可运行。
"""

import asyncio
import logging
import warnings
from typing import Any

from ..config import settings
from ..scheduler import (
    Commander,
    Planner,
    Dispatcher,
    SoldierPool,
    UserRequest,
)

logger = logging.getLogger(__name__)


class AgentWrapper:
    """
    旧 Agent API 的包装器

    警告：此类已弃用，仅用于向后兼容。
    请迁移到新的作战指挥室架构。
    """

    def __init__(self, name: str | None = None, api_key: str | None = None, brain: Any = None):
        warnings.warn(
            "Agent 类已弃用，请迁移到作战指挥室架构。"
            "查看 docs/MIGRATION_GUIDE.md 了解详情。",
            DeprecationWarning,
            stacklevel=2,
        )

        self.name = name or settings.agent_name
        self._api_key = api_key
        self._brain = brain

        # 初始化作战指挥室组件（懒加载）
        self._commander: Commander | None = None
        self._planner: Planner | None = None
        self._dispatcher: Dispatcher | None = None
        self._soldier_pool: SoldierPool | None = None
        self._initialized = False

        # 保持一些旧的属性以兼容
        self.ralph = _DeprecatedRalphLoop()
        self.memory_manager = _DeprecatedMemoryManager()

    async def _initialize(self) -> None:
        """懒初始化作战指挥室组件"""
        if self._initialized:
            return

        logger.info("Initializing Battle Room compatibility layer")

        self._planner = Planner()
        self._soldier_pool = SoldierPool()
        await self._soldier_pool.initialize()

        self._dispatcher = Dispatcher(self._soldier_pool)
        self._commander = Commander(self._planner, self._dispatcher)

        await self._commander.start()
        self._initialized = True

    async def chat_with_session(
        self,
        session_id: str,
        user_message: str,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """
        旧 API 的 chat_with_session 方法

        内部使用新的作战指挥室架构。
        """
        await self._initialize()

        if self._commander is None:
            raise RuntimeError("Commander not initialized")

        # 创建用户请求
        import uuid

        request = UserRequest(
            request_id=str(uuid.uuid4()),
            user_id="legacy_user",
            content=user_message,
            session_id=session_id,
        )

        # 提交给指挥官
        mission_id = await self._commander.receive_request(request)

        # 等待完成（简化版本）
        # 实际应该有更优雅的等待机制
        await asyncio.sleep(2)

        # 获取结果
        mission = await self._commander.get_mission_status(mission_id)

        if mission and mission.result:
            return str(mission.result)
        elif mission and mission.error:
            return f"Error: {mission.error}"
        else:
            return "Task submitted to Battle Room (compatibility mode)"

    async def shutdown(self) -> None:
        """关闭包装器"""
        if self._commander:
            await self._commander.stop()
        if self._soldier_pool:
            await self._soldier_pool.shutdown()
        self._initialized = False


class _DeprecatedRalphLoop:
    """已弃用的 RalphLoop 占位符"""

    def __init__(self, *args: Any, **kwargs: Any):
        warnings.warn(
            "RalphLoop 已弃用，其功能已集成到 Commander。"
            "查看 docs/MIGRATION_GUIDE.md 了解详情。",
            DeprecationWarning,
            stacklevel=2,
        )

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        warnings.warn(
            "RalphLoop.run() 已弃用，请使用 Commander。",
            DeprecationWarning,
            stacklevel=2,
        )
        return None


class _DeprecatedMemoryManager:
    """已弃用的 MemoryManager 占位符"""

    def __init__(self, *args: Any, **kwargs: Any):
        warnings.warn(
            "MemoryManager API 可能会变更，请查看新架构文档。",
            DeprecationWarning,
            stacklevel=2,
        )
