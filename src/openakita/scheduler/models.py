"""
作战指挥室数据模型

定义所有核心数据结构：用户请求、任务、计划、命令、执行结果等。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..protocols.reporting import ReportStatus, MissionId, TaskId, SoldierId

logger = logging.getLogger(__name__)


class MissionStatus(Enum):
    """任务整体状态"""

    PENDING = "pending"
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def active_states(cls) -> frozenset["MissionStatus"]:
        """返回活跃状态"""
        return frozenset({cls.PLANNING, cls.IN_PROGRESS, cls.PAUSED})

    @property
    def is_active(self) -> bool:
        """是否为活跃状态"""
        return self in self.active_states()

    @property
    def is_terminal(self) -> bool:
        """是否为终端状态"""
        return self in (
            self.COMPLETED,
            self.FAILED,
            self.CANCELLED,
        )


@dataclass
class UserRequest:
    """用户请求"""

    request_id: str
    user_id: str
    content: str
    context: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    session_id: str | None = None


@dataclass
class MissionTask:
    """单个任务"""

    task_id: TaskId
    mission_id: MissionId
    description: str
    dependencies: list[TaskId] = field(default_factory=list)
    priority: int = 0
    max_steps: int = 10
    timeout_seconds: int = 300
    assigned_to: SoldierId | None = None
    retry_count: int = 0
    max_retries: int = 3
    strategy_index: int = 0
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class MissionPlan:
    """任务计划（DAG）"""

    mission_id: MissionId
    tasks: list[MissionTask]
    dag: dict[TaskId, list[TaskId]]  # task_id -> [dependency_task_ids]
    strategies: list[dict[str, Any]] = field(default_factory=list)
    current_strategy_index: int = 0
    estimated_duration_seconds: int = 0
    created_at: datetime = field(default_factory=datetime.now)

    def get_ready_tasks(self, completed_tasks: set[TaskId]) -> list[MissionTask]:
        """获取所有依赖已满足的任务"""
        ready = []
        for task in self.tasks:
            if task.task_id in completed_tasks:
                continue
            all_deps_met = all(dep in completed_tasks for dep in task.dependencies)
            if all_deps_met:
                ready.append(task)
        return sorted(ready, key=lambda t: (-t.priority, t.created_at))

    def is_complete(self, completed_tasks: set[TaskId]) -> bool:
        """检查是否所有任务都已完成"""
        return all(task.task_id in completed_tasks for task in self.tasks)


@dataclass
class Order:
    """给军人的命令"""

    order_id: str
    task_id: TaskId
    mission_id: MissionId
    description: str
    max_steps: int = 10
    strategy: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ExecutionResult:
    """军人执行结果"""

    success: bool
    task_id: TaskId
    status: ReportStatus
    result: Any = None
    error: str | None = None
    steps_used: int = 0
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_report(cls, report: "StatusReport") -> "ExecutionResult":  # type: ignore # noqa: F821
        """从状态汇报创建执行结果"""
        return cls(
            success=report.status == ReportStatus.COMPLETED,
            task_id=report.task_id,
            status=report.status,
            result=report.result,
            error=report.error,
            steps_used=report.steps_used,
            duration_seconds=0.0,
        )
