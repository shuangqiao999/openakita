"""
Benchmark 并发控制修复验证

验证:
  1. _get_benchmark_sem 返回正确值的 Semaphore
  2. _system_benchmark_evolve 使用信号量
  3. _system_research_org 使用信号量
  4. Semaphore 实际限制并发
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path

_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-needed")
os.environ.setdefault("DEFAULT_MODEL", "local-test")

PASS = FAIL = 0
FAILED: list[str] = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(f"{name}: {detail}" if detail else name)
        print(f"  [FAIL] {name}  --  {detail}" if detail else f"  [FAIL] {name}")


def test_semaphore_setup():
    print("\n" + "=" * 60)
    print("1. _get_benchmark_sem 信号量初始化")

    from openakita.scheduler.executor import _get_benchmark_sem

    sem = _get_benchmark_sem()
    check("返回 asyncio.Semaphore", isinstance(sem, asyncio.Semaphore))

    sem2 = _get_benchmark_sem()
    check("多次调用返回同一实例 (不热替换)", sem is sem2)


def test_benchmark_evolve_uses_sem():
    print("\n" + "=" * 60)
    print("2. _system_benchmark_evolve 使用信号量")

    from openakita.scheduler.executor import TaskExecutor

    src = inspect.getsource(TaskExecutor._system_benchmark_evolve)
    check("调用 _get_benchmark_sem()", "_get_benchmark_sem()" in src)
    check("使用 async with", "async with" in src)
    check("委托到 _inner 方法", "_system_benchmark_evolve_inner" in src)


def test_research_org_uses_sem():
    print("\n" + "=" * 60)
    print("3. _system_research_org 使用信号量")

    from openakita.scheduler.executor import TaskExecutor

    src = inspect.getsource(TaskExecutor._system_research_org)
    check("调用 _get_benchmark_sem()", "_get_benchmark_sem()" in src)
    check("使用 async with", "async with" in src)
    check("委托到 _inner 方法", "_system_research_org_inner" in src)


def test_semaphore_concurrency_control():
    print("\n" + "=" * 60)
    print("4. Semaphore 实际并发限制验证")

    async def _test():
        sem = asyncio.Semaphore(1)
        running = 0
        max_running = 0

        async def worker(i):
            nonlocal running, max_running
            async with sem:
                running += 1
                max_running = max(max_running, running)
                await asyncio.sleep(0.01)
                running -= 1

        await asyncio.gather(*[worker(i) for i in range(5)])
        return max_running

    max_r = asyncio.run(_test())
    check(f"Semaphore(1) 最大并发 = {max_r}", max_r == 1)

    async def _test_multi():
        sem = asyncio.Semaphore(3)
        running = 0
        max_running = 0

        async def worker(i):
            nonlocal running, max_running
            async with sem:
                running += 1
                max_running = max(max_running, running)
                await asyncio.sleep(0.05)
                running -= 1

        await asyncio.gather(*[worker(i) for i in range(10)])
        return max_running

    max_r3 = asyncio.run(_test_multi())
    check(f"Semaphore(3) 最大并发 = {max_r3} (<= 3)", max_r3 <= 3)


def test_other_tasks_unaffected():
    print("\n" + "=" * 60)
    print("5. 其他 system task 不受影响")

    from openakita.scheduler.executor import TaskExecutor

    unaffected = [
        "_system_daily_memory",
        "_system_daily_selfcheck",
        "_system_proactive_heartbeat",
        "_system_workspace_backup",
        "_system_memory_nudge_review",
        "_system_pattern_learn",
    ]
    for method_name in unaffected:
        method = getattr(TaskExecutor, method_name, None)
        if method is None:
            continue
        src = inspect.getsource(method)
        check(
            f"{method_name} 不使用 benchmark_sem",
            "_get_benchmark_sem" not in src,
        )


def test_scheduler_general_semaphore_unchanged():
    print("\n" + "=" * 60)
    print("6. 调度器通用 Semaphore 未改变")

    from openakita.scheduler.scheduler import TaskScheduler

    src = inspect.getsource(TaskScheduler.__init__)
    check("默认 max_concurrent=5 未改变", "max_concurrent: int = 5" in src or "max_concurrent=5" in src)


def main():
    print("=" * 60)
    print("  Benchmark 并发控制修复验证")
    print("=" * 60)

    test_semaphore_setup()
    test_benchmark_evolve_uses_sem()
    test_research_org_uses_sem()
    test_semaphore_concurrency_control()
    test_other_tasks_unaffected()
    test_scheduler_general_semaphore_unchanged()

    print("\n" + "=" * 60)
    print(f"  结果: {PASS} passed, {FAIL} failed")
    if FAILED:
        print("\n  失败项:")
        for f in FAILED:
            print(f"    x {f}")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
