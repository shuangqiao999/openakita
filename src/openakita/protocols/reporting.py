"""
统一汇报协议模块

定义作战指挥室模型中使用的统一状态码、汇报数据结构和命令结构。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# 类型别名
MissionId = str
TaskId = str
SoldierId = str


class ReportStatus(Enum):
    """统一汇报状态码"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_CLARIFICATION = "needs_clarification"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    STEPS_EXHAUSTED = "steps_exhausted"

    @classmethod
    def terminal_states(cls) -> frozenset["ReportStatus"]:
        """返回终端状态集合"""
        return frozenset(
            {
                cls.COMPLETED,
                cls.FAILED,
                cls.CANCELLED,
                cls.TIMEOUT,
                cls.STEPS_EXHAUSTED,
            }
        )

    @property
    def is_terminal(self) -> bool:
        """是否为终端状态"""
        return self in self.terminal_states()


class CommandType(Enum):
    """指挥官命令类型"""

    CONTINUE = "continue"
    RETRY = "retry"
    RETRY_WITH_NEW_STRATEGY = "retry_with_new_strategy"
    REDIRECT = "redirect"
    DEGRADE = "degrade"
    CANCEL = "cancel"
    PAUSE = "pause"
    RESUME = "resume"
    CLARIFY = "clarify"
    REQUEST_HUMAN_INTERVENTION = "request_human_intervention"


@dataclass
class StatusReport:
    """标准汇报数据结构"""

    mission_id: MissionId
    task_id: TaskId
    soldier_id: SoldierId
    status: ReportStatus
    progress: float = 0.0  # 0.0 - 1.0
    message: str | None = None
    error: str | None = None
    result: Any = None
    steps_used: int = 0
    max_steps: int = 10
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """验证数据有效性"""
        if not (0.0 <= self.progress <= 1.0):
            logger.warning(f"Progress {self.progress} out of range, clamping")
            self.progress = max(0.0, min(1.0, self.progress))


@dataclass
class Command:
    """指挥官命令数据结构"""

    command_type: CommandType
    mission_id: MissionId
    task_id: TaskId | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_cancel(self) -> bool:
        """是否为取消命令"""
        return self.command_type == CommandType.CANCEL

    @property
    def is_pause(self) -> bool:
        """是否为暂停命令"""
        return self.command_type == CommandType.PAUSE

    @property
    def needs_human(self) -> bool:
        """是否需要人工介入"""
        return self.command_type == CommandType.REQUEST_HUMAN_INTERVENTION
