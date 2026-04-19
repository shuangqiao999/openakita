"""
作战指挥室架构增强模块

包含三项核心能力：
1. 指挥官经验学习（基于现有记忆系统）
2. 渐进自动化（信任等级系统）
3. 自愈能力（多层恢复策略）
"""

from .trust import (
    TrustLevel,
    TrustScore,
    TrustAction,
    TrustManager,
)
from .retry import (
    ExponentialBackoffRetry,
    retry_with_backoff,
)
from .health import (
    HealthStatus,
    HealthChecker,
)
from .snapshot import (
    StateSnapshot,
    SnapshotManager,
)
from .commander_memory import (
    CommanderMemoryExtension,
    get_commander_memory,
)

__all__ = [
    "TrustLevel",
    "TrustScore",
    "TrustAction",
    "TrustManager",
    "ExponentialBackoffRetry",
    "retry_with_backoff",
    "HealthStatus",
    "HealthChecker",
    "StateSnapshot",
    "SnapshotManager",
    "CommanderMemoryExtension",
    "get_commander_memory",
]
