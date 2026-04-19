"""
自愈能力 - 健康检查体系

为以下组件设计健康检查：
- 军人Agent健康检查
- 调度台健康检查
- 指挥官健康检查
- 记忆系统健康检查
- LLM连接健康检查
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """健康状态"""

    HEALTHY = "healthy"
    WARNING = "warning"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """健康检查结果"""

    component_name: str
    status: HealthStatus
    message: str = ""
    response_time_ms: float = 0.0
    error_count: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComponentHealthConfig:
    """组件健康检查配置"""

    check_interval_seconds: float = 30.0
    timeout_seconds: float = 5.0
    unhealthy_threshold: int = 3
    warning_threshold_response_ms: float = 1000.0
    unhealthy_threshold_response_ms: float = 5000.0
    warning_threshold_error_rate: float = 0.1
    unhealthy_threshold_error_rate: float = 0.2


class HealthChecker:
    """健康检查器"""

    def __init__(self):
        self._checks: dict[str, Callable[[], asyncio.Future[HealthCheckResult]]] = {}
        self._configs: dict[str, ComponentHealthConfig] = {}
        self._results: dict[str, HealthCheckResult] = {}
        self._consecutive_failures: dict[str, int] = {}
        self._running = False
        self._check_tasks: dict[str, asyncio.Task] = {}
        self._on_status_change_callbacks: list[
            Callable[[str, HealthCheckResult, HealthCheckResult], None]
        ] = []

    def register_check(
        self,
        component_name: str,
        check_fn: Callable[[], asyncio.Future[HealthCheckResult]],
        config: ComponentHealthConfig | None = None,
    ) -> None:
        """注册健康检查"""
        self._checks[component_name] = check_fn
        self._configs[component_name] = config or ComponentHealthConfig()
        self._consecutive_failures[component_name] = 0
        logger.info(f"Registered health check for: {component_name}")

    def register_status_change_callback(
        self,
        callback: Callable[[str, HealthCheckResult, HealthCheckResult], None],
    ) -> None:
        """注册状态变化回调"""
        self._on_status_change_callbacks.append(callback)

    async def start(self) -> None:
        """启动健康检查"""
        if self._running:
            logger.warning("HealthChecker already running")
            return

        self._running = True

        # 为每个检查启动定期任务
        for component_name, check_fn in self._checks.items():
            config = self._configs[component_name]
            self._check_tasks[component_name] = asyncio.create_task(
                self._check_loop(component_name, check_fn, config)
            )

        logger.info("HealthChecker started")

    async def stop(self) -> None:
        """停止健康检查"""
        self._running = False

        # 取消所有检查任务
        for task in self._check_tasks.values():
            task.cancel()

        # 等待任务完成
        if self._check_tasks:
            await asyncio.gather(*self._check_tasks.values(), return_exceptions=True)

        self._check_tasks.clear()
        logger.info("HealthChecker stopped")

    async def check_all(self) -> dict[str, HealthCheckResult]:
        """立即执行所有检查"""
        results = {}
        for component_name, check_fn in list(self._checks.items()):
            try:
                result = await check_fn()
                results[component_name] = result
            except Exception as e:
                logger.error(f"Health check failed for {component_name}: {e}", exc_info=True)
                results[component_name] = HealthCheckResult(
                    component_name=component_name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"Check failed: {e}",
                )
        return results

    def get_result(self, component_name: str) -> HealthCheckResult | None:
        """获取检查结果"""
        return self._results.get(component_name)

    def get_all_results(self) -> dict[str, HealthCheckResult]:
        """获取所有结果"""
        return dict(self._results)

    def get_overall_status(self) -> HealthStatus:
        """获取整体健康状态"""
        if not self._results:
            return HealthStatus.UNKNOWN

        statuses = [r.status for r in self._results.values()]

        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        elif HealthStatus.WARNING in statuses:
            return HealthStatus.WARNING
        return HealthStatus.HEALTHY

    async def _check_loop(
        self,
        component_name: str,
        check_fn: Callable[[], asyncio.Future[HealthCheckResult]],
        config: ComponentHealthConfig,
    ) -> None:
        """检查循环"""
        while self._running:
            try:
                old_result = self._results.get(component_name)

                # 执行检查（带超时）
                start_time = time.time()
                try:
                    result = await asyncio.wait_for(
                        check_fn(), timeout=config.timeout_seconds
                    )
                    result.response_time_ms = (time.time() - start_time) * 1000
                except asyncio.TimeoutError:
                    result = HealthCheckResult(
                        component_name=component_name,
                        status=HealthStatus.UNHEALTHY,
                        message=f"Check timeout after {config.timeout_seconds}s",
                        response_time_ms=config.timeout_seconds * 1000,
                    )
                except Exception as e:
                    result = HealthCheckResult(
                        component_name=component_name,
                        status=HealthStatus.UNHEALTHY,
                        message=f"Check error: {e}",
                    )

                # 更新状态
                self._results[component_name] = result

                # 处理连续失败
                if result.status == HealthStatus.UNHEALTHY:
                    self._consecutive_failures[component_name] += 1
                else:
                    self._consecutive_failures[component_name] = 0

                # 通知回调
                if old_result and old_result.status != result.status:
                    for callback in self._on_status_change_callbacks:
                        try:
                            callback(component_name, old_result, result)
                        except Exception as e:
                            logger.error(
                                f"Status change callback error: {e}", exc_info=True
                            )

                # 等待下一检查周期
                await asyncio.sleep(config.check_interval_seconds)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Check loop error for {component_name}: {e}", exc_info=True)
                await asyncio.sleep(5)


# ============== 便捷检查创建函数 ==============


def create_ping_check(
    component_name: str,
    ping_fn: Callable[[], asyncio.Future[bool]],
    warning_threshold_ms: float = 1000.0,
    unhealthy_threshold_ms: float = 5000.0,
) -> Callable[[], asyncio.Future[HealthCheckResult]]:
    """创建 Ping 检查"""

    async def check() -> HealthCheckResult:
        start_time = time.time()
        try:
            success = await ping_fn()
            response_time_ms = (time.time() - start_time) * 1000

            if success:
                if response_time_ms > unhealthy_threshold_ms:
                    status = HealthStatus.UNHEALTHY
                    message = f"Response too slow: {response_time_ms:.0f}ms"
                elif response_time_ms > warning_threshold_ms:
                    status = HealthStatus.WARNING
                    message = f"Response slow: {response_time_ms:.0f}ms"
                else:
                    status = HealthStatus.HEALTHY
                    message = "OK"
            else:
                status = HealthStatus.UNHEALTHY
                message = "Ping failed"
                response_time_ms = (time.time() - start_time) * 1000

            return HealthCheckResult(
                component_name=component_name,
                status=status,
                message=message,
                response_time_ms=response_time_ms,
            )
        except Exception as e:
            return HealthCheckResult(
                component_name=component_name,
                status=HealthStatus.UNHEALTHY,
                message=f"Ping error: {e}",
            )

    return check


def create_queue_check(
    component_name: str,
    queue_size_fn: Callable[[], int],
    warning_threshold: int = 50,
    unhealthy_threshold: int = 100,
) -> Callable[[], asyncio.Future[HealthCheckResult]]:
    """创建队列长度检查"""

    async def check() -> HealthCheckResult:
        try:
            size = queue_size_fn()

            if size > unhealthy_threshold:
                status = HealthStatus.UNHEALTHY
                message = f"Queue too long: {size}"
            elif size > warning_threshold:
                status = HealthStatus.WARNING
                message = f"Queue long: {size}"
            else:
                status = HealthStatus.HEALTHY
                message = f"Queue size: {size}"

            return HealthCheckResult(
                component_name=component_name,
                status=status,
                message=message,
                metadata={"queue_size": size},
            )
        except Exception as e:
            return HealthCheckResult(
                component_name=component_name,
                status=HealthStatus.UNHEALTHY,
                message=f"Queue check error: {e}",
            )

    return check
