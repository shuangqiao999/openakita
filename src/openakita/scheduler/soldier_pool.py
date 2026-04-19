"""
军人池 (SoldierPool) - 军人实例管理

职责：
- 管理军人 Agent 实例池
- 负载均衡
- 实例回收
- 健康检查
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from ..agents.soldier import SoldierAgent
from ..config import settings

logger = logging.getLogger(__name__)

# 类型别名
SoldierId = str


class SoldierStatus(Enum):
    """军人状态"""

    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    RECYCLED = "recycled"


@dataclass
class SoldierRecord:
    """军人记录"""

    soldier_id: SoldierId
    agent: SoldierAgent
    status: SoldierStatus = SoldierStatus.IDLE
    current_task_id: str | None = None
    last_used_at: datetime = field(default_factory=datetime.now)
    created_at: datetime = field(default_factory=datetime.now)
    task_count: int = 0
    error_count: int = 0


class SoldierPool:
    """军人池 - 管理军人 Agent 实例"""

    def __init__(
        self,
        min_size: int = 1,
        max_size: int = 10,
        idle_timeout_seconds: int = 1800,  # 30 分钟
    ):
        self.min_size = min_size
        self.max_size = max_size
        self.idle_timeout = timedelta(seconds=idle_timeout_seconds)

        self._soldiers: dict[SoldierId, SoldierRecord] = {}
        self._lock = asyncio.Lock()
        self._running = False

    async def initialize(self) -> None:
        """初始化军人池，预创建最小数量的军人"""
        if self._running:
            return

        self._running = True
        logger.info(f"Initializing soldier pool: min={self.min_size}, max={self.max_size}")

        for _ in range(self.min_size):
            await self._create_soldier()

        # 启动回收线程
        asyncio.create_task(self._recycle_loop())
        logger.info("Soldier pool initialized")

    async def shutdown(self) -> None:
        """关闭军人池"""
        self._running = False

        # 回收所有军人
        async with self._lock:
            for soldier_id in list(self._soldiers.keys()):
                await self._recycle_soldier(soldier_id)

        logger.info("Soldier pool shutdown")

    async def get_idle_soldier(self) -> SoldierId | None:
        """
        获取一个空闲的军人

        Returns:
            SoldierId | None: 军人 ID，如果没有空闲军人则返回 None
        """
        async with self._lock:
            # 先尝试找空闲军人
            for soldier_id, record in self._soldiers.items():
                if record.status == SoldierStatus.IDLE:
                    return soldier_id

            # 没有空闲，尝试创建新的
            if len(self._soldiers) < self.max_size:
                return await self._create_soldier()

            return None

    async def get_soldier(self, soldier_id: SoldierId) -> SoldierAgent:
        """
        获取指定的军人实例

        Args:
            soldier_id: 军人 ID

        Returns:
            SoldierAgent: 军人实例
        """
        async with self._lock:
            if soldier_id not in self._soldiers:
                raise ValueError(f"Soldier not found: {soldier_id}")

            record = self._soldiers[soldier_id]
            record.status = SoldierStatus.BUSY
            record.last_used_at = datetime.now()
            return record.agent

    async def release_soldier(self, soldier_id: SoldierId, success: bool = True) -> None:
        """
        释放军人回池

        Args:
            soldier_id: 军人 ID
            success: 任务是否成功完成
        """
        async with self._lock:
            if soldier_id not in self._soldiers:
                logger.warning(f"Releasing unknown soldier: {soldier_id}")
                return

            record = self._soldiers[soldier_id]
            record.status = SoldierStatus.IDLE
            record.current_task_id = None
            record.task_count += 1
            if not success:
                record.error_count += 1

            logger.debug(
                f"Soldier released: {soldier_id}, "
                f"tasks={record.task_count}, errors={record.error_count}"
            )

    async def _create_soldier(self) -> SoldierId:
        """
        创建新的军人

        Returns:
            SoldierId: 新军人的 ID
        """
        import uuid

        soldier_id = f"soldier_{uuid.uuid4().hex[:8]}"

        try:
            # 创建军人 Agent
            agent = await self._create_soldier_agent(soldier_id)

            record = SoldierRecord(
                soldier_id=soldier_id,
                agent=agent,
                created_at=datetime.now(),
                last_used_at=datetime.now(),
            )

            self._soldiers[soldier_id] = record
            logger.info(f"Created new soldier: {soldier_id}")
            return soldier_id

        except Exception as e:
            logger.error(f"Failed to create soldier: {e}", exc_info=True)
            raise

    async def _create_soldier_agent(self, soldier_id: SoldierId) -> SoldierAgent:
        """
        创建军人 Agent 实例

        Args:
            soldier_id: 军人 ID

        Returns:
            SoldierAgent: 军人实例
        """
        # TODO: 这里需要根据实际情况创建 SoldierAgent
        # 暂时返回一个简单的实现
        from ..agents.soldier import SoldierAgent

        return SoldierAgent(soldier_id=soldier_id)

    async def _recycle_soldier(self, soldier_id: SoldierId) -> None:
        """
        回收军人

        Args:
            soldier_id: 军人 ID
        """
        if soldier_id not in self._soldiers:
            return

        record = self._soldiers[soldier_id]

        # 清理资源
        try:
            if hasattr(record.agent, "shutdown"):
                await record.agent.shutdown()
        except Exception as e:
            logger.error(f"Error shutting down soldier {soldier_id}: {e}", exc_info=True)

        record.status = SoldierStatus.RECYCLED
        del self._soldiers[soldier_id]
        logger.info(f"Soldier recycled: {soldier_id}")

    async def _recycle_loop(self) -> None:
        """回收循环 - 定期清理空闲超时的军人"""
        while self._running:
            try:
                await asyncio.sleep(60)  # 每分钟检查一次

                async with self._lock:
                    now = datetime.now()
                    to_recycle = []

                    for soldier_id, record in self._soldiers.items():
                        # 保持最小数量
                        if len(self._soldiers) - len(to_recycle) <= self.min_size:
                            break

                        if (
                            record.status == SoldierStatus.IDLE
                            and now - record.last_used_at > self.idle_timeout
                        ):
                            to_recycle.append(soldier_id)

                    for soldier_id in to_recycle:
                        await self._recycle_soldier(soldier_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Recycle loop error", exc_info=True)
