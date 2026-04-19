"""
自愈能力 - L1 局部重试（指数退避策略）

第一次失败后等待1秒重试，第二次2秒，第三次4秒，以此类推。
最大重试次数默认为3次，可配置。
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import Any, Callable, TypeVar, ParamSpec

logger = logging.getLogger(__name__)

T = TypeVar("T")
P = ParamSpec("P")


class RetryableErrorType(Enum):
    """可重试的错误类型"""

    NETWORK_TIMEOUT = "network_timeout"
    CONNECTION_RESET = "connection_reset"
    SERVICE_UNAVAILABLE = "service_unavailable"
    RATE_LIMIT = "rate_limit"
    TEMPORARY_FAILURE = "temporary_failure"


class NonRetryableErrorType(Enum):
    """不可重试的错误类型"""

    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    INVALID_ARGUMENT = "invalid_argument"
    NOT_FOUND = "not_found"


@dataclass
class RetryConfig:
    """重试配置"""

    max_retries: int = 3
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    backoff_multiplier: float = 2.0
    jitter_factor: float = 0.1  # 抖动因子，避免雪崩
    retryable_exceptions: tuple[type[Exception], ...] = (
        asyncio.TimeoutError,
        ConnectionError,
    )


class ExponentialBackoffRetry:
    """指数退避重试器"""

    def __init__(self, config: RetryConfig | None = None):
        self.config = config or RetryConfig()
        self._retry_count: int = 0
        self._last_error: Exception | None = None
        self._success_count: int = 0
        self._failure_count: int = 0

    def calculate_delay(self, attempt: int) -> float:
        """
        计算第 n 次重试的延迟时间

        指数退避公式：delay = initial * (multiplier ^ attempt)
        添加抖动因子避免多个客户端同时重试
        """
        import random

        delay = self.config.initial_delay_seconds * (
            self.config.backoff_multiplier**attempt
        )
        delay = min(delay, self.config.max_delay_seconds)

        # 添加抖动
        if self.config.jitter_factor > 0:
            jitter = random.uniform(
                -self.config.jitter_factor * delay,
                self.config.jitter_factor * delay,
            )
            delay = max(0, delay + jitter)

        return delay

    async def execute(
        self,
        func: Callable[P, asyncio.Future[T]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> T:
        """
        执行带重试的异步函数

        Args:
            func: 要执行的异步函数
            *args: 位置参数
            **kwargs: 关键字参数

        Returns:
            函数执行结果

        Raises:
            Exception: 超过重试次数后抛出最后一次异常
        """
        self._retry_count = 0
        self._last_error = None

        while self._retry_count <= self.config.max_retries:
            try:
                result = await func(*args, **kwargs)
                self._success_count += 1
                self._retry_count = 0
                return result

            except Exception as e:
                self._last_error = e
                self._failure_count += 1

                # 检查是否是可重试的异常
                if not self._is_retryable(e):
                    logger.warning(
                        f"Non-retryable error: {type(e).__name__}, giving up"
                    )
                    raise

                if self._retry_count >= self.config.max_retries:
                    logger.error(
                        f"Max retries ({self.config.max_retries}) exceeded, "
                        f"giving up. Last error: {e}"
                    )
                    raise

                # 计算延迟并等待
                delay = self.calculate_delay(self._retry_count)
                self._retry_count += 1

                logger.warning(
                    f"Retry {self._retry_count}/{self.config.max_retries} "
                    f"after error: {type(e).__name__}. "
                    f"Waiting {delay:.2f}s..."
                )

                await asyncio.sleep(delay)

        # 理论上不会到这里
        raise self._last_error or RuntimeError("Unexpected retry state")

    def _is_retryable(self, exc: Exception) -> bool:
        """判断异常是否可重试"""
        # 检查异常类型
        if isinstance(exc, self.config.retryable_exceptions):
            return True

        # 检查异常消息中的关键词
        exc_msg = str(exc).lower()
        retryable_keywords = [
            "timeout",
            "timed out",
            "connection reset",
            "service unavailable",
            "503",
            "504",
            "429",
            "rate limit",
            "too many requests",
            "temporary",
            "try again",
        ]

        for keyword in retryable_keywords:
            if keyword in exc_msg:
                return True

        return False

    @property
    def stats(self) -> dict[str, Any]:
        """获取统计信息"""
        return {
            "success_count": self._success_count,
            "failure_count": self._failure_count,
            "current_retry_count": self._retry_count,
        }


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_multiplier: float = 2.0,
    retryable_exceptions: tuple[type[Exception], ...] = (
        asyncio.TimeoutError,
        ConnectionError,
    ),
):
    """
    指数退避重试装饰器

    用法：
    ```python
    @retry_with_backoff(max_retries=3)
    async def my_operation():
        # ...
    ```
    """

    def decorator(func: Callable[P, asyncio.Future[T]]) -> Callable[P, asyncio.Future[T]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            config = RetryConfig(
                max_retries=max_retries,
                initial_delay_seconds=initial_delay,
                max_delay_seconds=max_delay,
                backoff_multiplier=backoff_multiplier,
                retryable_exceptions=retryable_exceptions,
            )
            retryer = ExponentialBackoffRetry(config)
            return await retryer.execute(func, *args, **kwargs)

        return wrapper

    return decorator


# 便捷函数
async def run_with_retry(
    func: Callable[P, asyncio.Future[T]],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """
    使用默认配置运行带重试的函数

    这是一个便捷函数，适合大多数场景。
    """
    retryer = ExponentialBackoffRetry()
    return await retryer.execute(func, *args, **kwargs)
