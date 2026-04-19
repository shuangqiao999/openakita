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
from typing import Any, Dict, List, Optional

from .soldier_types import SoldierInfo, SoldierStatus, SoldierAgentProtocol
from ..config import settings

logger = logging.getLogger(__name__)

# 类型别名
SoldierId = str


@dataclass
class SoldierRecord:
    """军人记录"""

    soldier_id: SoldierId
    agent: SoldierAgentProtocol
    status: SoldierStatus = SoldierStatus.IDLE
    current_task_id: str | None = None
    last_used_at: datetime = field(default_factory=datetime.now)
    created_at: datetime = field(default_factory=datetime.now)
    task_count: int = 0
    error_count: int = 0


class SoldierPool:
    """
    军人池 - 管理所有军人Agent的生命周期
    """
    
    def __init__(self, initial_size: int = 3, max_size: int = 10):
        self._soldiers: Dict[str, SoldierRecord] = {}
        self._max_size = max_size
        self._initial_size = initial_size
        self._lock = asyncio.Lock()
        self._running = False
    
    async def start(self) -> None:
        """启动军人池，创建初始军人"""
        if self._running:
            return
        
        self._running = True
        
        for i in range(self._initial_size):
            soldier = await self._create_soldier()
            self._soldiers[soldier.soldier_id] = soldier
        
        logger.info(f"SoldierPool started with {len(self._soldiers)} soldiers")
    
    async def stop(self) -> None:
        """停止军人池，关闭所有军人"""
        self._running = False
        
        for soldier in self._soldiers.values():
            await soldier.cancel()
        
        self._soldiers.clear()
        logger.info("SoldierPool stopped")
    
    async def _create_soldier(self) -> SoldierAgentProtocol:
        """
        创建新的军人Agent
        
        注意：使用延迟导入打破循环依赖
        """
        import uuid
        from ..agents.soldier import SoldierAgent
        
        soldier_id = f"soldier_{uuid.uuid4().hex[:8]}"
        soldier = SoldierAgent(soldier_id=soldier_id)
        return soldier
    
    async def acquire_soldier(self) -> Optional[SoldierAgentProtocol]:
        """
        获取一个空闲的军人
        
        Returns:
            空闲的军人，如果没有则返回 None
        """
        async with self._lock:
            for soldier in self._soldiers.values():
                if soldier.status == SoldierStatus.IDLE:
                    return soldier
            return None
    
    async def get_soldier(self, soldier_id: SoldierId) -> Optional[SoldierAgentProtocol]:
        """根据ID获取军人"""
        return self._soldiers.get(soldier_id)
    
    def get_all_soldiers(self) -> List[SoldierInfo]:
        """获取所有军人的信息（用于看板展示）"""
        return [soldier.get_info() for soldier in self._soldiers.values()]
    
    async def restart_soldier(self, soldier_id: SoldierId) -> bool:
        """重启指定的军人"""
        async with self._lock:
            old_soldier = self._soldiers.get(soldier_id)
            if old_soldier:
                await old_soldier.cancel()
            
            new_soldier = await self._create_soldier()
            self._soldiers[soldier_id] = new_soldier
            logger.info(f"Soldier restarted: {soldier_id}")
            return True
        
        return False
    
    def get_stats(self) -> dict:
        """获取军人池统计信息"""
        soldiers = list(self._soldiers.values())
        return {
            "total": len(soldiers),
            "idle": sum(1 for s in soldiers if s.status == SoldierStatus.IDLE),
            "running": sum(1 for s in soldiers if s.status == SoldierStatus.RUNNING),
            "blocked": sum(1 for s in soldiers if s.status == SoldierStatus.BLOCKED),
            "error": sum(1 for s in soldiers if s.status == SoldierStatus.ERROR),
        }
