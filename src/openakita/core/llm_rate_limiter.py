"""
Global LLM Rate Limiter — Token Bucket + Concurrency Semaphore.

Provides a global singleton that all Agent instances share, enforcing:
- RPM (requests per minute) rate via token bucket
- Max concurrent in-flight LLM calls via asyncio.Semaphore
- Automatic 429 backoff with penalty cooldown

Usage:
    limiter = GlobalLLMRateLimiter()
    await limiter.acquire()
    try:
        response = await llm_client.chat(...)
    except RateLimitError:
        limiter.report_rate_limited()
        raise
    finally:
        limiter.release()
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class GlobalLLMRateLimiter:
    """Thread-safe-asyncio singleton for LLM rate limiting.

    Token bucket refills at ``_rpm / 60`` tokens per second.
    """

    _instance: "GlobalLLMRateLimiter | None" = None
    _lock: asyncio.Lock

    def __new__(cls) -> "GlobalLLMRateLimiter":
        if cls._instance is None:
            inst = super().__new__(cls)
            cls._instance = inst
            inst._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        from ..config import settings

        self._rpm: float = float(getattr(settings, "llm_rate_limit_rpm", 0) or 0)
        self._max_concurrent: int = int(getattr(settings, "llm_max_concurrent", 8) or 8)
        self._semaphore = asyncio.Semaphore(max(1, self._max_concurrent))
        self._tokens: float = float(self._rpm) if self._rpm > 0 else 0.0
        self._last_refill: float = time.monotonic()
        self._bucket_lock = asyncio.Lock()
        self._penalty_until: float = 0.0
        self._initialized = True
        self._active_count: int = 0
        self._total_acquired: int = 0
        self._total_limited: int = 0
        self._total_penalties: int = 0

    # ── config hot-reload ──────────────────────────────────────────

    def refresh_config(self) -> None:
        from ..config import settings

        new_rpm = float(getattr(settings, "llm_rate_limit_rpm", 0) or 0)
        new_max = int(getattr(settings, "llm_max_concurrent", 8) or 8)
        if new_max != self._max_concurrent:
            self.adjust_concurrency(new_max)
        self._rpm = new_rpm
        logger.info(
            "[RateLimiter] Config refreshed: rpm=%d, max_concurrent=%d",
            int(self._rpm),
            self._max_concurrent,
        )

    def adjust_concurrency(self, new_limit: int) -> None:
        old = self._max_concurrent
        new_limit = max(1, min(new_limit, 64))
        self._max_concurrent = new_limit
        delta = new_limit - old
        if delta > 0:
            for _ in range(delta):
                self._semaphore.release()
        elif delta < 0:
            for _ in range(-delta):
                try:
                    if self._semaphore.locked():
                        continue
                    self._semaphore._value -= 1
                except Exception:
                    pass

    # ── acquire / release ──────────────────────────────────────────

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        self._active_count += 1
        self._total_acquired += 1

        if self._rpm <= 0:
            return

        async with self._bucket_lock:
            now = time.monotonic()
            if now < self._penalty_until:
                wait = self._penalty_until - now
                await asyncio.sleep(wait)
                now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(float(self._rpm), self._tokens + elapsed * (self._rpm / 60.0))
            self._last_refill = now
            if self._tokens < 1.0:
                self._total_limited += 1
                wait = (1.0 - self._tokens) * (60.0 / self._rpm)
                await asyncio.sleep(wait)
                self._tokens = 1.0
                self._last_refill = time.monotonic()
            self._tokens -= 1.0

    def release(self) -> None:
        self._semaphore.release()
        if self._active_count > 0:
            self._active_count -= 1

    def report_rate_limited(self) -> None:
        self._penalty_until = time.monotonic() + 5.0
        self._tokens = 0.0
        self._total_penalties += 1
        penalty_concurrency = max(1, self._max_concurrent // 2)
        self.adjust_concurrency(penalty_concurrency)
        logger.warning(
            "[RateLimiter] 429 penalty applied: tokens=0, concurrency=%d for 5s",
            penalty_concurrency,
        )

    # ── stats ──────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "rpm_limit": int(self._rpm),
            "max_concurrent": self._max_concurrent,
            "active_count": self._active_count,
            "tokens_available": round(self._tokens, 1),
            "total_acquired": self._total_acquired,
            "total_limited": self._total_limited,
            "total_penalties": self._total_penalties,
            "in_penalty": time.monotonic() < self._penalty_until,
        }
