"""
作战指挥室调度模块

包含指挥官、参谋部、调度台、情报看板等核心角色。
"""

# 先导出类型（无依赖）
from .soldier_types import SoldierInfo, SoldierStatus, SoldierAgentProtocol

# 再导出其他模块
from .models import (
    UserRequest,
    MissionTask,
    MissionPlan,
    Order,
    ExecutionResult,
    MissionStatus,
)
from .planner import Planner
from .dispatcher import Dispatcher, DispatchResult
from .soldier_pool import SoldierPool
from .commander import Commander
from .dashboard import Dashboard

__all__ = [
    # 类型
    "SoldierInfo",
    "SoldierStatus", 
    "SoldierAgentProtocol",
    # 核心类
    "Dispatcher",
    "SoldierPool",
    "Commander",
    "Planner",
    # 数据模型
    "UserRequest",
    "MissionTask",
    "MissionPlan",
    "Order",
    "ExecutionResult",
    "MissionStatus",
    "DispatchResult",
    "Dashboard",
]
