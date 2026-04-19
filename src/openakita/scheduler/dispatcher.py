"""
调度台 (Dispatcher) - 任务派发与监控

职责：
- 任务队列管理
- 派发任务到军人池
- 超时监控
- 负载均衡
- 军人健康监控

禁止：
- 不做决策
- 不修改计划
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from .models import MissionTask, Order, ExecutionResult
from .soldier_pool import SoldierPool, SoldierId
from ..protocols.reporting import ReportStatus, StatusReport

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """任务派发结果"""

    success: bool
    task_id: str
    soldier_id: SoldierId | None = None
    error: str | None = None


class Dispatcher:
    """调度台 - 负责任务派发和监控"""

    def __init__(self, soldier_pool: SoldierPool):
        self.soldier_pool = soldier_pool
        self._task_queue: asyncio.PriorityQueue[tuple[int, int, MissionTask]] = (
            asyncio.PriorityQueue()
        )
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._task_timeouts: dict[str, datetime] = {}
        self._report_callbacks: list[Callable[[StatusReport], None]] = []
        self._task_counter: int = 0
        self._running: bool = False

    def register_report_callback(self, callback: Callable[[StatusReport], None]) -> None:
        """注册状态汇报回调"""
        self._report_callbacks.append(callback)

    async def start(self) -> None:
        """启动调度器"""
        if self._running:
            logger.warning("Dispatcher already running")
            return

        self._running = True
        asyncio.create_task(self._dispatch_loop())
        asyncio.create_task(self._timeout_monitor_loop())
        logger.info("Dispatcher started")

    async def stop(self) -> None:
        """停止调度器"""
        self._running = False

        # 取消所有运行中的任务
        for task_id, task in list(self._running_tasks.items()):
            task.cancel()
            del self._running_tasks[task_id]

        logger.info("Dispatcher stopped")

    async def dispatch_task(
        self, task: MissionTask, soldier_id: SoldierId | None = None
    ) -> DispatchResult:
        """
        派发单个任务

        Args:
            task: 要派发的任务
            soldier_id: 指定的军人 ID（可选）

        Returns:
            DispatchResult: 派发结果
        """
        logger.info(f"Dispatching task: task_id={task.task_id}")

        if soldier_id is None:
            # 负载均衡：选择空闲军人
            soldier_id = await self.soldier_pool.get_idle_soldier()

        if soldier_id is None:
            # 无空闲军人，加入队列
            self._task_counter += 1
            await self._task_queue.put(
                (-task.priority, self._task_counter, task)  # 负优先级用于升序
            )
            logger.info(f"Task queued: task_id={task.task_id}")
            return DispatchResult(
                success=False,
                task_id=task.task_id,
                error="No idle soldiers available, queued",
            )

        # 创建执行任务
        return await self._dispatch_to_soldier(task, soldier_id)

    async def _dispatch_to_soldier(
        self, task: MissionTask, soldier_id: SoldierId
    ) -> DispatchResult:
        """将任务派发给指定军人"""
        order = Order(
            order_id=f"order_{task.task_id}",
            task_id=task.task_id,
            mission_id=task.mission_id,
            description=task.description,
            max_steps=task.max_steps,
        )

        async def _execute_wrapper() -> ExecutionResult:
            try:
                soldier = await self.soldier_pool.get_soldier(soldier_id)
                result = await soldier.execute(order)

                # 创建汇报
                report = StatusReport(
                    mission_id=task.mission_id,
                    task_id=task.task_id,
                    soldier_id=soldier_id,
                    status=result.status,
                    progress=1.0 if result.success else 0.0,
                    result=result.result,
                    error=result.error,
                    steps_used=result.steps_used,
                    max_steps=task.max_steps,
                )

                # 通知所有回调
                for callback in self._report_callbacks:
                    try:
                        callback(report)
                    except Exception as e:
                        logger.error(f"Report callback error: {e}", exc_info=True)

                return result
            except asyncio.CancelledError:
                logger.info(f"Task cancelled: task_id={task.task_id}")
                return ExecutionResult(
                    success=False,
                    task_id=task.task_id,
                    status=ReportStatus.CANCELLED,
                    error="Task cancelled",
                )
            except Exception as e:
                logger.error(f"Task execution failed: {task.task_id}", exc_info=True)
                return ExecutionResult(
                    success=False,
                    task_id=task.task_id,
                    status=ReportStatus.FAILED,
                    error=str(e),
                )

        exec_task = asyncio.create_task(_execute_wrapper())
        self._running_tasks[task.task_id] = exec_task
        self._task_timeouts[task.task_id] = datetime.now() + timedelta(
            seconds=task.timeout_seconds
        )

        return DispatchResult(
            success=True,
            task_id=task.task_id,
            soldier_id=soldier_id,
        )

    async def cancel_task(self, task_id: str) -> None:
        """取消任务"""
        if task_id in self._running_tasks:
            self._running_tasks[task_id].cancel()
            del self._running_tasks[task_id]
        if task_id in self._task_timeouts:
            del self._task_timeouts[task_id]
        logger.info(f"Task cancelled: task_id={task_id}")

    async def _dispatch_loop(self) -> None:
        """任务派发循环"""
        while self._running:
            try:
                # 检查是否有空闲军人
                idle_soldier = await self.soldier_pool.get_idle_soldier()
                if idle_soldier is None:
                    await asyncio.sleep(0.1)
                    continue

                # 从队列获取任务（带超时）
                try:
                    _, _, task = await asyncio.wait_for(
                        self._task_queue.get(), timeout=0.1
                    )
                    await self._dispatch_to_soldier(task, idle_soldier)
                except asyncio.TimeoutError:
                    pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Dispatch loop error", exc_info=True)
                await asyncio.sleep(1.0)

    async def _timeout_monitor_loop(self) -> None:
        """超时监控循环"""
        while self._running:
            try:
                now = datetime.now()
                for task_id, timeout_at in list(self._task_timeouts.items()):
                    if now >= timeout_at:
                        logger.warning(f"Task timeout: task_id={task_id}")
                        await self.cancel_task(task_id)

                        # 触发超时汇报（简单版本）
                        # 实际应该从军人那里获取 soldier_id
                        for callback in self._report_callbacks:
                            try:
                                report = StatusReport(
                                    mission_id="",  # 需要实际任务信息
                                    task_id=task_id,
                                    soldier_id="",
                                    status=ReportStatus.TIMEOUT,
                                    error="Task timeout",
                                )
                                callback(report)
                            except Exception as e:
                                logger.error(f"Timeout report error: {e}", exc_info=True)

                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Timeout monitor error", exc_info=True)
                await asyncio.sleep(1.0)
