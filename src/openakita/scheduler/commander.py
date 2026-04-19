"""
指挥官 (Commander) - 全局决策层

职责：
- 接收用户请求
- 监听状态汇报
- 做出决策（继续/重派/取消/人工介入）
- 全局资源协调
- 配置策略管理

核心价值观（升华自 Ralph Loop）：
- 永不放弃任务目标，但会不断换策略、换路径、降级或请求人工
- 不放弃的是最终目标，而非机械地重复同一方法

禁止：
- 不执行具体任务
- 不调用工具
- 不生成内容
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from ..config import settings
from ..protocols.reporting import (
    ReportStatus,
    CommandType,
    StatusReport,
    Command,
    MissionId,
    TaskId,
)
from .models import (
    UserRequest,
    MissionPlan,
    MissionStatus,
    MissionTask,
)
from .planner import Planner
from .dispatcher import Dispatcher

logger = logging.getLogger(__name__)


class DecisionMode(Enum):
    """决策模式"""

    FULL_AUTO = "full_auto"
    FULL_MANUAL = "full_manual"
    HYBRID = "hybrid"


@dataclass
class CommanderConfig:
    """指挥官配置"""

    decision_mode: DecisionMode = DecisionMode.HYBRID
    max_strategy_attempts: int = 3
    auto_retry_threshold: int = 1
    allow_degradation: bool = True
    human_intervention_timeout_seconds: int = 3600
    timeout_action: str = "continue_auto"


@dataclass
class MissionRecord:
    """任务记录"""

    mission_id: MissionId
    request: UserRequest
    plan: MissionPlan
    status: MissionStatus = MissionStatus.PENDING
    completed_tasks: set[TaskId] = field(default_factory=set)
    failed_tasks: dict[TaskId, list[str]] = field(default_factory=dict)
    current_strategy_index: int = 0
    total_attempts: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    result: Any = None
    error: str | None = None


class Commander:
    """
    指挥官 - 全局决策中心

    永不放弃任务目标，但会不断换策略、换路径、降级或请求人工。
    """

    def __init__(
        self,
        planner: Planner,
        dispatcher: Dispatcher,
        config: CommanderConfig | None = None,
    ):
        self.planner = planner
        self.dispatcher = dispatcher
        self.config = config or CommanderConfig()

        self._missions: dict[MissionId, MissionRecord] = {}
        self._human_intervention_queue: asyncio.Queue[MissionId] = asyncio.Queue()
        self._status_callbacks: list[Callable[[MissionRecord], None]] = []
        self._running = False

        # 注册汇报回调
        self.dispatcher.register_report_callback(self._handle_report)

    def register_status_callback(self, callback: Callable[[MissionRecord], None]) -> None:
        """注册状态变更回调"""
        self._status_callbacks.append(callback)

    async def start(self) -> None:
        """启动指挥官"""
        if self._running:
            logger.warning("Commander already running")
            return

        self._running = True
        await self.dispatcher.start()
        logger.info("Commander started")

    async def stop(self) -> None:
        """停止指挥官"""
        self._running = False
        await self.dispatcher.stop()
        logger.info("Commander stopped")

    async def receive_request(self, request: UserRequest) -> MissionId:
        """
        接收用户请求

        Args:
            request: 用户请求

        Returns:
            MissionId: 任务 ID
        """
        mission_id = f"mission_{uuid.uuid4().hex[:12]}"
        logger.info(f"Receiving request: mission_id={mission_id}")

        # 创建任务记录
        record = MissionRecord(
            mission_id=mission_id,
            request=request,
            plan=MissionPlan(
                mission_id=mission_id,
                tasks=[],
                dag={},
            ),
            status=MissionStatus.PLANNING,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        self._missions[mission_id] = record
        self._notify_status_change(record)

        # 异步执行规划和执行
        asyncio.create_task(self._process_mission(mission_id))

        return mission_id

    async def _process_mission(self, mission_id: MissionId) -> None:
        """处理任务"""
        try:
            record = self._missions[mission_id]

            # 阶段 1: 规划
            logger.info(f"Planning mission: {mission_id}")
            record.status = MissionStatus.PLANNING
            self._notify_status_change(record)

            plan = await self.planner.decompose_task(record.request)
            record.plan = plan
            record.current_strategy_index = plan.current_strategy_index

            # 阶段 2: 执行
            logger.info(f"Executing mission: {mission_id}")
            record.status = MissionStatus.IN_PROGRESS
            self._notify_status_change(record)

            await self._execute_mission(record)

        except Exception as e:
            logger.error(f"Mission failed: {mission_id}", exc_info=True)
            if mission_id in self._missions:
                record = self._missions[mission_id]
                record.status = MissionStatus.FAILED
                record.error = str(e)
                self._notify_status_change(record)

    async def _execute_mission(self, record: MissionRecord) -> None:
        """执行任务计划"""
        while record.status == MissionStatus.IN_PROGRESS:
            # 获取可执行的任务
            ready_tasks = record.plan.get_ready_tasks(record.completed_tasks)

            if not ready_tasks:
                # 检查是否完成
                if record.plan.is_complete(record.completed_tasks):
                    record.status = MissionStatus.COMPLETED
                    self._notify_status_change(record)
                    logger.info(f"Mission completed: {record.mission_id}")
                    return

                # 等待
                await asyncio.sleep(0.1)
                continue

            # 派发任务
            for task in ready_tasks:
                await self.dispatcher.dispatch_task(task)

            # 等待
            await asyncio.sleep(0.1)

    def _handle_report(self, report: StatusReport) -> None:
        """处理状态汇报"""
        logger.debug(
            f"Received report: mission={report.mission_id}, "
            f"task={report.task_id}, status={report.status}"
        )

        if report.mission_id not in self._missions:
            logger.warning(f"Unknown mission in report: {report.mission_id}")
            return

        record = self._missions[report.mission_id]

        # 更新记录
        record.updated_at = datetime.now()

        if report.status.is_terminal:
            if report.status == ReportStatus.COMPLETED:
                record.completed_tasks.add(report.task_id)
            else:
                # 处理失败
                self._handle_failure(record, report)

        self._notify_status_change(record)

    def _handle_failure(self, record: MissionRecord, report: StatusReport) -> None:
        """处理失败情况 - 永不放弃的核心决策逻辑"""
        logger.warning(
            f"Task failed: mission={record.mission_id}, "
            f"task={report.task_id}, error={report.error}"
        )

        # 记录失败
        if report.task_id not in record.failed_tasks:
            record.failed_tasks[report.task_id] = []
        record.failed_tasks[report.task_id].append(report.error or "unknown")
        record.total_attempts += 1

        # 三层决策策略
        strategy_name = record.plan.strategies[record.current_strategy_index].get(
            "name", "default"
        )
        failures_on_this_strategy = len(record.failed_tasks.get(report.task_id, []))

        if failures_on_this_strategy < self.config.auto_retry_threshold:
            # 策略 1: 同一策略重试
            logger.info(f"Retrying with same strategy: {strategy_name}")
            asyncio.create_task(self._retry_task(record, report.task_id))

        elif record.current_strategy_index < len(record.plan.strategies) - 1:
            # 策略 2: 换策略
            record.current_strategy_index += 1
            new_strategy = record.plan.strategies[record.current_strategy_index]
            logger.info(
                f"Switching strategy: {strategy_name} -> {new_strategy.get('name')}"
            )
            asyncio.create_task(self._retry_task(record, report.task_id))

        elif self.config.allow_degradation:
            # 策略 3: 降级
            logger.info(f"Attempting degradation: {record.mission_id}")
            asyncio.create_task(self._try_degradation(record))

        else:
            # 所有策略耗尽，请求人工介入
            logger.warning(f"All strategies exhausted, requesting human intervention")
            record.status = MissionStatus.WAITING_FOR_HUMAN
            asyncio.create_task(self._human_intervention_queue.put(record.mission_id))

    async def _retry_task(self, record: MissionRecord, task_id: TaskId) -> None:
        """重试任务"""
        for task in record.plan.tasks:
            if task.task_id == task_id:
                task.strategy_index = record.current_strategy_index
                task.retry_count += 1
                await self.dispatcher.dispatch_task(task)
                break

    async def _try_degradation(self, record: MissionRecord) -> None:
        """尝试降级执行"""
        record.plan = await self.planner.generate_fallback_plan(
            record.plan, {"failed_tasks": record.failed_tasks}
        )
        record.current_strategy_index = record.plan.current_strategy_index

        # 重置失败任务状态并重新执行
        for task_id in list(record.failed_tasks.keys()):
            if task_id not in record.completed_tasks:
                for task in record.plan.tasks:
                    if task.task_id == task_id:
                        await self.dispatcher.dispatch_task(task)
                        break

    async def cancel_mission(self, mission_id: MissionId) -> None:
        """取消任务"""
        if mission_id not in self._missions:
            logger.warning(f"Mission not found: {mission_id}")
            return

        record = self._missions[mission_id]
        record.status = MissionStatus.CANCELLED

        # 取消所有相关任务
        for task in record.plan.tasks:
            await self.dispatcher.cancel_task(task.task_id)

        self._notify_status_change(record)
        logger.info(f"Mission cancelled: {mission_id}")

    async def pause_mission(self, mission_id: MissionId) -> bool:
        """暂停任务"""
        if mission_id not in self._missions:
            logger.warning(f"Mission not found: {mission_id}")
            return False

        record = self._missions[mission_id]
        if record.status != MissionStatus.IN_PROGRESS:
            logger.warning(f"Cannot pause mission in status: {record.status}")
            return False

        record.status = MissionStatus.PAUSED
        self._notify_status_change(record)
        logger.info(f"Mission paused: {mission_id}")
        return True

    async def resume_mission(self, mission_id: MissionId) -> bool:
        """恢复任务"""
        if mission_id not in self._missions:
            logger.warning(f"Mission not found: {mission_id}")
            return False

        record = self._missions[mission_id]
        if record.status != MissionStatus.PAUSED:
            logger.warning(f"Cannot resume mission in status: {record.status}")
            return False

        record.status = MissionStatus.IN_PROGRESS
        self._notify_status_change(record)
        logger.info(f"Mission resumed: {mission_id}")
        asyncio.create_task(self._execute_mission(record))
        return True

    async def retry_mission(self, mission_id: MissionId) -> bool:
        """重试任务"""
        if mission_id not in self._missions:
            logger.warning(f"Mission not found: {mission_id}")
            return False

        record = self._missions[mission_id]
        if record.status not in (MissionStatus.FAILED, MissionStatus.CANCELLED):
            logger.warning(f"Cannot retry mission in status: {record.status}")
            return False

        record.status = MissionStatus.IN_PROGRESS
        record.completed_tasks.clear()
        record.failed_tasks.clear()
        record.total_attempts = 0
        record.current_strategy_index = 0
        record.error = None
        record.result = None
        record.updated_at = datetime.now()
        self._notify_status_change(record)
        logger.info(f"Mission retried: {mission_id}")
        asyncio.create_task(self._execute_mission(record))
        return True

    async def get_mission_status(self, mission_id: MissionId) -> MissionRecord | None:
        """获取任务状态"""
        return self._missions.get(mission_id)

    def _notify_status_change(self, record: MissionRecord) -> None:
        """通知状态变更"""
        for callback in self._status_callbacks:
            try:
                callback(record)
            except Exception as e:
                logger.error(f"Status callback error: {e}", exc_info=True)
