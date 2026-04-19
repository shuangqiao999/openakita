"""
军人相关类型定义
用于打破 soldier.py 和 soldier_pool.py 之间的循环导入
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Protocol

from ..protocols.reporting import SoldierId


class SoldierStatus(Enum):
    """军人状态"""
    IDLE = "idle"           # 空闲
    RUNNING = "running"     # 执行中
    BLOCKED = "blocked"     # 阻塞（等待资源）
    PAUSED = "paused"       # 暂停
    ERROR = "error"         # 错误
    STOPPED = "stopped"     # 已停止


@dataclass
class SoldierInfo:
    """军人信息（用于跨模块传递，不包含方法）"""
    soldier_id: SoldierId
    name: str
    status: SoldierStatus
    current_task_id: Optional[str] = None
    current_task_name: Optional[str] = None
    progress: float = 0.0
    steps_used: int = 0
    max_steps: int = 10
    elapsed_time: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    last_heartbeat: datetime = field(default_factory=datetime.now)


class SoldierAgentProtocol(Protocol):
    """
    军人Agent协议（类型协议，用于类型检查）
    定义 SoldierAgent 必须实现的方法，避免直接导入类
    """
    
    @property
    def soldier_id(self) -> SoldierId: ...
    
    @property
    def name(self) -> str: ...
    
    @property
    def status(self) -> SoldierStatus: ...
    
    def get_info(self) -> SoldierInfo: ...
    
    async def execute_task(self, task: Any) -> Any: ...
    
    async def pause(self) -> None: ...
    
    async def resume(self) -> None: ...
    
    async def cancel(self) -> None: ...
    
    async def heartbeat(self) -> bool: ...
