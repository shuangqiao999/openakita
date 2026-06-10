"""
Benchmark 评估引擎

定义标准化任务集，评估 Agent 系统性能，产出量化指标用于驱动进化决策。

改进:
- P0-1: asyncio.wait_for 超时控制
- P0-2: _verify_outcome 关键词匹配验证
- P0-3: 临时文件清理
- P1-4: 归一化效率分公式
- P1-5: asyncio.Semaphore 并发执行
- P2-6: 首次自动保存基线
- P2-7: task_runner / token_counter 接口解耦
- P2-8: 数据路径从配置读取
"""

from __future__ import annotations

import asyncio
import glob as _glob
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import tempfile as _tempfile

_BENCHMARK_TEMP_PATTERNS = [
    str(Path(_tempfile.gettempdir()) / "bench_test*"),
    str(Path(_tempfile.gettempdir()) / "benchmark_*"),
    str(Path(_tempfile.gettempdir()) / "fib_*"),
]


@dataclass
class BenchmarkTask:
    id: str
    description: str
    category: str
    expected_outcome: str
    max_tokens: int = 50000
    timeout_seconds: int = 120
    difficulty: str = "medium"


@dataclass
class BenchmarkResult:
    task_id: str
    success: bool
    tokens_used: int = 0
    time_seconds: float = 0.0
    tool_calls: int = 0
    iterations: int = 0
    error: str | None = None
    output_summary: str = ""
    verification_passed: bool | None = None
    verification_reason: str = ""


@dataclass
class BenchmarkMetrics:
    success_rate: float = 0.0
    avg_tokens: float = 0.0
    avg_time: float = 0.0
    avg_tool_calls: float = 0.0
    efficiency_score: float = 0.0
    category_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class BenchmarkReport:
    timestamp: str = ""
    results: list[BenchmarkResult] = field(default_factory=list)
    metrics: BenchmarkMetrics = field(default_factory=BenchmarkMetrics)
    baseline_delta: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "metrics": {
                "success_rate": self.metrics.success_rate,
                "avg_tokens": self.metrics.avg_tokens,
                "avg_time": self.metrics.avg_time,
                "avg_tool_calls": self.metrics.avg_tool_calls,
                "efficiency_score": self.metrics.efficiency_score,
                "category_scores": self.metrics.category_scores,
            },
            "results": [
                {
                    "task_id": r.task_id,
                    "success": r.success,
                    "tokens_used": r.tokens_used,
                    "time_seconds": r.time_seconds,
                    "verification_passed": r.verification_passed,
                    "verification_reason": r.verification_reason,
                }
                for r in self.results
            ],
            "baseline_delta": self.baseline_delta,
        }


_DEFAULT_BENCHMARK_TASKS: list[dict[str, Any]] = [
    {
        "id": "tool-file-edit",
        "description": "创建文件 /tmp/bench_test.py 内容为 print('hello')，然后读取验证内容正确",
        "category": "tool_use",
        "expected_outcome": "文件创建成功且内容为 print('hello')",
        "timeout_seconds": 30,
        "difficulty": "easy",
    },
    {
        "id": "tool-shell-exec",
        "description": '运行 python -c "print(sum(range(100)))" 并报告结果',
        "category": "tool_use",
        "expected_outcome": "输出结果为 4950",
        "timeout_seconds": 30,
        "difficulty": "easy",
    },
    {
        "id": "code-fibonacci",
        "description": "编写 Python 函数计算第 N 个斐波那契数(迭代法)，写入文件并运行验证 fib(10)=55",
        "category": "coding",
        "expected_outcome": "函数正确实现，fib(10) 输出 55",
        "timeout_seconds": 60,
        "difficulty": "easy",
    },
    {
        "id": "code-bug-fix",
        "description": (
            "以下代码有 bug: `def avg(lst): return sum(lst) / len(lst)` "
            "当 lst 为空时会除零。修复它并写测试验证"
        ),
        "category": "coding",
        "expected_outcome": "函数处理空列表不报错，测试通过",
        "timeout_seconds": 60,
        "difficulty": "medium",
    },
    {
        "id": "research-web",
        "description": "搜索 Python 3.12 的主要新特性，列出至少 3 个具体特性名称",
        "category": "research",
        "expected_outcome": "返回至少 3 个 Python 3.12 新特性",
        "timeout_seconds": 90,
        "difficulty": "medium",
    },
    {
        "id": "memory-store-recall",
        "description": "记住以下信息: '基准测试密码是 BenchMark2026'。然后立即回忆这个信息",
        "category": "memory",
        "expected_outcome": "成功存储并回忆出 'BenchMark2026'",
        "timeout_seconds": 30,
        "difficulty": "easy",
    },
    {
        "id": "writing-summary",
        "description": (
            "用中文写一段 50-100 字的摘要，概括'机器学习是人工智能的一个分支，"
            "通过数据和算法让计算机自动改进性能'这句话的含义"
        ),
        "category": "writing",
        "expected_outcome": "产出中文摘要，包含'机器学习'和'人工智能'",
        "timeout_seconds": 30,
        "difficulty": "easy",
    },
    {
        "id": "code-refactor",
        "description": (
            "重构以下代码使其更简洁: "
            "`result = []; for i in range(10): if i % 2 == 0: result.append(i*i)` "
            "改为列表推导式"
        ),
        "category": "coding",
        "expected_outcome": "使用列表推导式",
        "timeout_seconds": 45,
        "difficulty": "easy",
    },
]


class BenchmarkEngine:
    AUTO_BASELINE_THRESHOLD = 0.8

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        task_runner: Callable[..., Any] | None = None,
        token_counter: Callable[..., int] | None = None,
        max_concurrent: int = 3,
    ) -> None:
        if data_dir is None:
            try:
                from openakita.config import settings

                data_dir = settings.data_dir / "evolution" / "benchmarks"
            except Exception:
                data_dir = Path("data/evolution/benchmarks")
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._tasks_file = self._data_dir / "tasks.json"
        self._results_dir = self._data_dir / "results"
        self._results_dir.mkdir(parents=True, exist_ok=True)
        self._task_runner = task_runner or self._default_task_runner
        self._token_counter = token_counter or self._default_token_counter
        self._max_concurrent = max(1, max_concurrent)

    def load_tasks(self) -> list[BenchmarkTask]:
        if self._tasks_file.exists():
            try:
                data = json.loads(self._tasks_file.read_text(encoding="utf-8"))
                return [BenchmarkTask(**t) for t in data]
            except Exception as e:
                logger.warning("[Benchmark] 加载自定义任务失败: %s, 使用默认", e)
        return [BenchmarkTask(**t) for t in _DEFAULT_BENCHMARK_TASKS]

    async def run_suite(
        self,
        agent: Any,
        tasks: list[BenchmarkTask] | None = None,
        max_concurrent: int | None = None,
    ) -> BenchmarkReport:
        if tasks is None:
            tasks = self.load_tasks()
        mc = max_concurrent or self._max_concurrent
        sem = asyncio.Semaphore(mc)

        async def _guarded(task: BenchmarkTask) -> BenchmarkResult:
            async with sem:
                return await self._run_single(agent, task)

        try:
            results = list(await asyncio.gather(*[_guarded(t) for t in tasks]))
        finally:
            self._cleanup_temp_files()

        for task, result in zip(tasks, results, strict=False):
            logger.info(
                "[Benchmark] %s: %s (%.1fs, %d tok, verify=%s)",
                task.id,
                "PASS" if result.success else "FAIL",
                result.time_seconds,
                result.tokens_used,
                result.verification_passed,
            )

        metrics = self._compute_metrics(results, tasks)
        report = BenchmarkReport(
            timestamp=datetime.now().isoformat(),
            results=results,
            metrics=metrics,
        )

        baseline = self._load_latest_baseline()
        if baseline:
            report.baseline_delta = self._compute_delta(baseline, metrics)
        elif metrics.success_rate >= self.AUTO_BASELINE_THRESHOLD:
            self.save_as_baseline(report)
            logger.info(
                "[Benchmark] 首次基线自动保存 (成功率 %.0f%%)",
                metrics.success_rate * 100,
            )

        self._save_report(report)
        return report

    async def _run_single(self, agent: Any, task: BenchmarkTask) -> BenchmarkResult:
        t0 = time.perf_counter()
        try:
            if (
                not hasattr(agent, "execute_task_from_message")
                and self._task_runner is self._default_task_runner
            ):
                return BenchmarkResult(
                    task_id=task.id,
                    success=False,
                    error="Agent 缺少 execute_task_from_message 方法",
                )

            tokens_before = self._token_counter(agent)

            try:
                coro = self._task_runner(agent, task.description)
                result = await asyncio.wait_for(coro, timeout=task.timeout_seconds)
            except TimeoutError:
                elapsed = time.perf_counter() - t0
                return BenchmarkResult(
                    task_id=task.id,
                    success=False,
                    time_seconds=elapsed,
                    error=f"超时 ({task.timeout_seconds}s)",
                )

            elapsed = time.perf_counter() - t0
            success = getattr(result, "success", False) if result else False
            iterations = getattr(result, "iterations", 0) or 0
            output = str(getattr(result, "data", ""))[:500]

            tokens_after = self._token_counter(agent)
            tokens = max(0, tokens_after - tokens_before)

            verification_passed = None
            verification_reason = ""
            if success and task.expected_outcome:
                verification_passed, verification_reason = self._verify_outcome(task, output)
                if not verification_passed:
                    success = False

            return BenchmarkResult(
                task_id=task.id,
                success=success,
                tokens_used=tokens,
                time_seconds=elapsed,
                tool_calls=0,
                iterations=iterations,
                output_summary=output,
                verification_passed=verification_passed,
                verification_reason=verification_reason,
                error=verification_reason if not success and verification_reason else None,
            )
        except Exception as e:
            elapsed = time.perf_counter() - t0
            return BenchmarkResult(
                task_id=task.id,
                success=False,
                time_seconds=elapsed,
                error=str(e),
            )
        finally:
            pass  # cleanup moved to suite-level

    def _verify_outcome(self, task: BenchmarkTask, output: str) -> tuple[bool, str]:
        if not task.expected_outcome or not output:
            return True, ""

        expected = task.expected_outcome
        output_lower = output.lower()

        quoted = re.findall(
            r"['\"\u2018\u2019\u201c\u201d]([^'\"\u2018\u2019\u201c\u201d]+)['\"\u2018\u2019\u201c\u201d]",
            expected,
        )
        for kw in quoted:
            if kw.lower() not in output_lower:
                return False, f"输出缺少关键内容: '{kw}'"

        numbers = re.findall(r"\b\d{2,}\b", expected)
        for num in numbers:
            if num not in output:
                return False, f"输出缺少数值: {num}"

        return True, ""

    def _cleanup_temp_files(self) -> None:
        for pattern in _BENCHMARK_TEMP_PATTERNS:
            for path_str in _glob.glob(pattern):
                try:
                    p = Path(path_str)
                    if p.is_file():
                        p.unlink(missing_ok=True)
                    elif p.is_dir():
                        import shutil

                        shutil.rmtree(p, ignore_errors=True)
                except Exception:
                    pass

    def _compute_metrics(
        self, results: list[BenchmarkResult], tasks: list[BenchmarkTask]
    ) -> BenchmarkMetrics:
        if not results:
            return BenchmarkMetrics()

        n = len(results)
        success_rate = sum(1 for r in results if r.success) / n

        all_tokens = [r.tokens_used for r in results if r.tokens_used > 0]
        all_times = [r.time_seconds for r in results if r.time_seconds > 0]
        all_tools = [r.tool_calls for r in results]

        avg_tokens = sum(all_tokens) / len(all_tokens) if all_tokens else 0
        avg_time = sum(all_times) / len(all_times) if all_times else 0
        avg_tool_calls = sum(all_tools) / n if n else 0

        baseline = self._load_latest_baseline()
        if baseline and baseline.avg_tokens > 0 and avg_tokens > 0:
            token_ratio = min(0.5, (avg_tokens / baseline.avg_tokens) * 0.3)
            efficiency = success_rate * 100 * (1 - token_ratio)
        else:
            efficiency = success_rate * 100

        categories: dict[str, list[bool]] = {}
        for task, result in zip(tasks, results, strict=False):
            categories.setdefault(task.category, []).append(result.success)
        cat_scores = {cat: sum(v) / len(v) for cat, v in categories.items()}

        return BenchmarkMetrics(
            success_rate=success_rate,
            avg_tokens=avg_tokens,
            avg_time=avg_time,
            avg_tool_calls=avg_tool_calls,
            efficiency_score=efficiency,
            category_scores=cat_scores,
        )

    def _compute_delta(
        self, baseline: BenchmarkMetrics, current: BenchmarkMetrics
    ) -> dict[str, float]:
        return {
            "success_rate": current.success_rate - baseline.success_rate,
            "avg_tokens": current.avg_tokens - baseline.avg_tokens,
            "avg_time": current.avg_time - baseline.avg_time,
            "efficiency_score": current.efficiency_score - baseline.efficiency_score,
        }

    def _load_latest_baseline(self) -> BenchmarkMetrics | None:
        baseline_file = self._data_dir / "baseline.json"
        if baseline_file.exists():
            try:
                data = json.loads(baseline_file.read_text(encoding="utf-8"))
                m = data.get("metrics", {})
                return BenchmarkMetrics(
                    success_rate=m.get("success_rate", 0),
                    avg_tokens=m.get("avg_tokens", 0),
                    avg_time=m.get("avg_time", 0),
                    avg_tool_calls=m.get("avg_tool_calls", 0),
                    efficiency_score=m.get("efficiency_score", 0),
                )
            except Exception:
                pass
        results = sorted(self._results_dir.glob("*.json"), reverse=True)
        if not results:
            return None
        try:
            data = json.loads(results[0].read_text(encoding="utf-8"))
            m = data.get("metrics", {})
            return BenchmarkMetrics(
                success_rate=m.get("success_rate", 0),
                avg_tokens=m.get("avg_tokens", 0),
                avg_time=m.get("avg_time", 0),
                avg_tool_calls=m.get("avg_tool_calls", 0),
                efficiency_score=m.get("efficiency_score", 0),
            )
        except Exception:
            return None

    def _save_report(self, report: BenchmarkReport) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._results_dir / f"{ts}_benchmark.json"
        path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_as_baseline(self, report: BenchmarkReport) -> None:
        path = self._data_dir / "baseline.json"
        path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    async def _default_task_runner(agent: Any, description: str) -> Any:
        return await agent.execute_task_from_message(description)

    @staticmethod
    def _default_token_counter(agent: Any) -> int:
        brain = getattr(agent, "brain", None)
        return getattr(brain, "total_tokens_used", 0) if brain else 0
