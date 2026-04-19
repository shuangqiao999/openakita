"""
军人 Agent (SoldierAgent) - 无条件执行层

职责：
- 无条件执行分派任务
- 严格按照命令执行
- 上报状态（进度/完成/失败/需澄清）
- 有最大执行步数限制（默认 10 步）

禁止：
- 不决策
- 不擅自重试
- 不修改任务目标
- 不自主调用其他 Agent
- 不自主切换策略
"""

import asyncio
import logging
import time
from typing import Any, Callable

from ..config import settings
from ..protocols.reporting import ReportStatus, SoldierId
from ..scheduler.models import Order, ExecutionResult

logger = logging.getLogger(__name__)


class SoldierAgent:
    """
    军人 Agent - 执行层

    严格按照命令执行任务，不做任何决策，遇到问题直接上报。
    """

    def __init__(self, soldier_id: SoldierId):
        self.soldier_id = soldier_id
        self._current_order: Order | None = None
        self._step_count: int = 0
        self._max_steps: int = getattr(settings, "soldier_max_steps", 10)
        self._cancel_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # 默认不暂停
        self._report_callback: Callable[..., Any] | None = None

    def set_report_callback(self, callback: Callable[..., Any]) -> None:
        """设置状态汇报回调"""
        self._report_callback = callback

    async def execute(self, order: Order) -> ExecutionResult:
        """
        执行命令

        Args:
            order: 执行命令

        Returns:
            ExecutionResult: 执行结果
        """
        logger.info(f"[{self.soldier_id}] Executing order: {order.order_id}")

        self._current_order = order
        self._step_count = 0
        self._max_steps = order.max_steps
        self._cancel_event.clear()

        start_time = time.time()

        try:
            # 执行前上报
            await self._report_status(
                status=ReportStatus.IN_PROGRESS,
                progress=0.0,
                message="Starting execution",
            )

            # 执行任务
            result = await self._execute_task(order)

            duration = time.time() - start_time

            # 执行完成
            await self._report_status(
                status=ReportStatus.COMPLETED,
                progress=1.0,
                message="Execution completed",
                result=result,
            )

            return ExecutionResult(
                success=True,
                task_id=order.task_id,
                status=ReportStatus.COMPLETED,
                result=result,
                steps_used=self._step_count,
                duration_seconds=duration,
            )

        except asyncio.CancelledError:
            duration = time.time() - start_time
            logger.info(f"[{self.soldier_id}] Execution cancelled")

            await self._report_status(
                status=ReportStatus.CANCELLED,
                progress=min(self._step_count / self._max_steps, 1.0),
                message="Execution cancelled",
            )

            return ExecutionResult(
                success=False,
                task_id=order.task_id,
                status=ReportStatus.CANCELLED,
                error="Cancelled",
                steps_used=self._step_count,
                duration_seconds=duration,
            )

        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"[{self.soldier_id}] Execution failed", exc_info=True)

            await self._report_status(
                status=ReportStatus.FAILED,
                progress=min(self._step_count / self._max_steps, 1.0),
                message=f"Execution failed: {str(e)}",
                error=str(e),
            )

            return ExecutionResult(
                success=False,
                task_id=order.task_id,
                status=ReportStatus.FAILED,
                error=str(e),
                steps_used=self._step_count,
                duration_seconds=duration,
            )

        finally:
            self._current_order = None

    async def _execute_task(self, order: Order) -> Any:
        """
        真正的任务执行逻辑

        TODO: 集成 ReasoningEngine 实现完整功能
        集成步骤：
        1. 初始化 Brain、ToolExecutor、ContextManager 等组件
        2. 创建 ReasoningEngine 实例
        3. 调用 reasoning_engine.run() 执行任务
        4. 跟踪步数，确保不超过 max_steps
        5. 定期上报进度

        Args:
            order: 执行命令

        Returns:
            Any: 执行结果

        Raises:
            Exception: 执行失败时抛出异常
        """
        # TODO: 这里需要集成现有的 ReasoningEngine 等
        # 暂时返回一个占位实现
        logger.warning(f"[{self.soldier_id}] Using placeholder execution logic - ReasoningEngine integration pending")

        # 模拟执行步骤
        for i in range(5):
            if self._cancel_event.is_set():
                raise asyncio.CancelledError()

            await self._pause_event.wait()

            self._step_count += 1

            # 检查步数上限
            if self._step_count >= self._max_steps:
                await self._report_status(
                    status=ReportStatus.STEPS_EXHAUSTED,
                    progress=1.0,
                    message=f"Max steps ({self._max_steps}) reached",
                )
                raise RuntimeError(f"Max steps reached: {self._max_steps}")

            # 上报进度
            await self._report_status(
                status=ReportStatus.IN_PROGRESS,
                progress=self._step_count / self._max_steps,
                message=f"Step {self._step_count}/{self._max_steps}",
            )

            await asyncio.sleep(0.1)

        return f"Placeholder result for: {order.description}"

    async def cancel(self) -> None:
        """取消执行"""
        logger.info(f"[{self.soldier_id}] Cancel requested")
        self._cancel_event.set()

    async def pause(self) -> None:
        """暂停执行"""
        logger.info(f"[{self.soldier_id}] Pause requested")
        self._pause_event.clear()

    async def resume(self) -> None:
        """恢复执行"""
        logger.info(f"[{self.soldier_id}] Resume requested")
        self._pause_event.set()

    async def _report_status(
        self,
        status: ReportStatus,
        progress: float,
        message: str | None = None,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        """
        上报状态

        Args:
            status: 状态
            progress: 进度 (0.0-1.0)
            message: 消息
            result: 结果
            error: 错误信息
        """
        if self._report_callback and self._current_order:
            from ..protocols.reporting import StatusReport

            report = StatusReport(
                mission_id=self._current_order.mission_id,
                task_id=self._current_order.task_id,
                soldier_id=self.soldier_id,
                status=status,
                progress=progress,
                message=message,
                result=result,
                error=error,
                steps_used=self._step_count,
                max_steps=self._max_steps,
            )

            try:
                if asyncio.iscoroutinefunction(self._report_callback):
                    await self._report_callback(report)
                else:
                    self._report_callback(report)
            except Exception as e:
                logger.error(f"[{self.soldier_id}] Failed to report status", exc_info=True)

    async def shutdown(self) -> None:
        """关闭军人 Agent"""
        logger.info(f"[{self.soldier_id}] Shutting down")
        self._cancel_event.set()
        self._pause_event.set()


# 向后兼容别名
Soldier = SoldierAgent
