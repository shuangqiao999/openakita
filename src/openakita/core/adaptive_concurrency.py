"""
Adaptive Concurrency Controller.

Monitors LLM API latency P95 and 429 error rate, dynamically adjusts
the global concurrency limit up or down to prevent rate-limiting while
maximising throughput.

Usage:
    ctrl = AdaptiveConcurrencyController()
    await ctrl.start()
    # ... during operation, feed metrics ...
    ctrl.record_latency(seconds)
    if rate_limited: ctrl.record_rate_limited()
    # ...controller auto-adjusts ...
    await ctrl.stop()
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

_DEFAULT_EVAL_INTERVAL = 10.0
_LATENCY_WINDOW = 30
_HIGH_LATENCY_THRESHOLD = 5.0
_LOW_LATENCY_THRESHOLD = 2.0
_HIGH_ERROR_RATE = 0.1
_TARGET_ERROR_RATE = 0.0


class AdaptiveConcurrencyController:
    def __init__(
        self,
        eval_interval: float | None = None,
        initial_concurrency: int = 8,
        min_concurrency: int = 1,
        max_concurrency: int = 64,
    ):
        self._eval_interval = eval_interval or _DEFAULT_EVAL_INTERVAL
        self._initial_concurrency = initial_concurrency
        self._min_concurrency = min_concurrency
        self._max_concurrency = max_concurrency
        self._current_limit = initial_concurrency

        self._latencies: list[float] = []
        self._error_count: int = 0
        self._total_count: int = 0
        self._last_eval: float = time.monotonic()

        self._running = False
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ── metrics feeding ──────────────────────────────────────────

    def record_latency(self, seconds: float) -> None:
        now = time.monotonic()
        self._latencies.append(seconds)
        self._total_count += 1
        cutoff = now - _LATENCY_WINDOW
        self._latencies = [lt for lt in self._latencies if lt > cutoff]

    def record_rate_limited(self) -> None:
        self._error_count += 1
        self._total_count += 1

    # ── lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._eval_loop(), name="adaptive_concurrency")
        logger.info(
            "[AdaptiveConcurrency] Started: initial=%d, min=%d, max=%d",
            self._current_limit,
            self._min_concurrency,
            self._max_concurrency,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[AdaptiveConcurrency] Stopped")

    async def _eval_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._eval_interval)
                await self._evaluate()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[AdaptiveConcurrency] Eval error: {e}")

    # ── core logic ───────────────────────────────────────────────

    async def _evaluate(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._latencies = [lt for lt in self._latencies if lt > now - _LATENCY_WINDOW]

            if not self._latencies:
                return

            sorted_lats = sorted(self._latencies)
            idx = int(len(sorted_lats) * 0.95)
            p95 = sorted_lats[idx] if idx < len(sorted_lats) else sorted_lats[-1]

            error_rate = self._error_count / max(self._total_count, 1)
            recent_total = self._total_count

            old_limit = self._current_limit

            if (
                p95 > _HIGH_LATENCY_THRESHOLD
                or error_rate > _HIGH_ERROR_RATE
            ):
                self._current_limit = max(self._min_concurrency, self._current_limit // 2)
                logger.warning(
                    "[AdaptiveConcurrency] Throttle: p95=%.1fs error_rate=%.0f%% "
                    "concurrency: %d -> %d",
                    p95,
                    error_rate * 100,
                    old_limit,
                    self._current_limit,
                )
            elif (
                p95 < _LOW_LATENCY_THRESHOLD
                and error_rate <= _TARGET_ERROR_RATE
                and self._current_limit < self._initial_concurrency
            ):
                self._current_limit = min(
                    self._initial_concurrency,
                    self._current_limit + max(1, self._current_limit // 5),
                )
                logger.info(
                    "[AdaptiveConcurrency] Scale up: p95=%.1fs error_rate=%.0f%% "
                    "concurrency: %d -> %d",
                    p95,
                    error_rate * 100,
                    old_limit,
                    self._current_limit,
                )

            if self._current_limit != old_limit:
                from .llm_rate_limiter import GlobalLLMRateLimiter

                GlobalLLMRateLimiter().adjust_concurrency(self._current_limit)

            if recent_total >= 50:
                self._error_count = 0
                self._total_count = 0

    # ── properties ───────────────────────────────────────────────

    @property
    def current_limit(self) -> int:
        return self._current_limit

    @property
    def p95_latency(self) -> float:
        if not self._latencies:
            return 0.0
        sorted_lats = sorted(self._latencies)
        idx = int(len(sorted_lats) * 0.95)
        return sorted_lats[idx] if idx < len(sorted_lats) else sorted_lats[-1]

    def get_stats(self) -> dict:
        return {
            "current_limit": self._current_limit,
            "min_limit": self._min_concurrency,
            "max_limit": self._max_concurrency,
            "p95_latency": round(self.p95_latency, 2),
            "error_rate": round(self._error_count / max(self._total_count, 1), 3),
            "samples": len(self._latencies),
        }
