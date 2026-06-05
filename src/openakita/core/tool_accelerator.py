"""
通用工具加速组件
提供熔断器（三态）、重试（含异常过滤）、缓存键生成。
供 ToolExecutor 调用，不通过装饰器侵入 handler。
"""

import asyncio
import hashlib
import json
import logging
import time
from enum import Enum, auto
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = auto()     # 正常通过
    OPEN = auto()       # 拒绝请求
    HALF_OPEN = auto()  # 允许一次试探


class CircuitBreaker:
    """三态熔断器：Closed → Open → Half-Open → Closed"""

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ):
        self.threshold = failure_threshold
        self.recovery = recovery_timeout
        self.failures = 0
        self.last_fail = 0.0
        self.state: CircuitState = CircuitState.CLOSED

    def allow_request(self) -> bool:
        """是否允许本次请求"""
        now = time.monotonic()
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if now - self.last_fail > self.recovery:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN: 只允许一次试探（外部调用 record_success/record_failure 后状态变化）
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        self.last_fail = time.monotonic()
        if self.state == CircuitState.HALF_OPEN:
            # 试探失败 → 立即重新打开
            self.state = CircuitState.OPEN
        elif self.failures >= self.threshold:
            self.state = CircuitState.OPEN


def make_cache_key(tool_name: str, params: dict) -> str | None:
    """生成工具参数缓存键。失败返回 None。"""
    filtered = {
        k: v
        for k, v in sorted(params.items())
        if k not in ("request_id", "session_id", "timestamp")
    }
    try:
        raw = json.dumps(filtered, sort_keys=True, default=str)
    except (TypeError, ValueError):
        try:
            raw = str(filtered)
            logger.warning(
                "[Accel] make_cache_key fallback to str() for %s", tool_name
            )
        except Exception:
            return None
    return hashlib.md5(raw.encode()).hexdigest()


# 默认只对网络 / 超时异常重试，参数错误立即失败
_DEFAULT_RETRYABLE: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    TimeoutError,
    ConnectionError,
    OSError,
)


async def run_with_retry(
    coro_factory: Callable[[], Any],
    *,
    max_retries: int = 1,
    delay: float = 0.1,
    timeout: float = 10.0,
    retry_on: Iterable[type[BaseException]] = _DEFAULT_RETRYABLE,
) -> Any:
    """
    带超时和智能重试的异步执行。

    Args:
        coro_factory: 返回可等待对象的工厂函数（每次调用创建新协程/任务）
        max_retries: 最大重试次数（总执行次数 = 1 + max_retries）
        delay: 重试间隔（秒）
        timeout: 单次尝试超时（秒）
        retry_on: 需要重试的异常类型元组。此集合外的异常会立即抛出。
    """
    last_exc: BaseException | None = None
    retry_set = tuple(retry_on)
    for attempt in range(max_retries + 1):
        try:
            return await asyncio.wait_for(coro_factory(), timeout=timeout)
        except asyncio.TimeoutError as e:
            last_exc = e
            logger.warning(
                "[Accel] attempt %d/%d timeout after %.1fs",
                attempt + 1, max_retries + 1, timeout,
            )
        except Exception as e:
            last_exc = e
            should_retry = isinstance(e, retry_set)
            if not should_retry:
                logger.debug(
                    "[Accel] non-retryable error, failing immediately: %s: %s",
                    type(e).__name__, e,
                )
                raise
            logger.warning(
                "[Accel] attempt %d/%d failed (%s: %s), retrying in %.1fs",
                attempt + 1, max_retries + 1, type(e).__name__, e, delay,
            )
        if attempt < max_retries:
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]
