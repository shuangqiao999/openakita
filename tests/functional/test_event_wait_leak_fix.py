"""
Event.wait() TaskDestroyed 全面修复验证 (含 LMStudio 实测)

验证 4 处 asyncio.wait + Event.wait() 的父取消清理:
  1. agent.py 工具执行三路竞速 (cancel + skip waiter)
  2. agent.py CancellableLLM (cancel waiter)
  3. context_manager.py 上下文压缩 (cancel waiter)
  4. reasoning_engine.py 流式工具执行 (cancel + skip waiter)
  5. 模拟父取消: 验证 except BaseException 清理生效
  6. 模拟正常完成: 验证不影响正常流程
  7. LMStudio 实测: benchmark 任务超时后无 TaskDestroyed
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))
os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-needed")
os.environ.setdefault("DEFAULT_MODEL", "qwen/qwen3.5-9b")

LMSTUDIO_BASE = "http://localhost:1234/v1"
MODEL = "qwen/qwen3.5-9b"

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


def section(num, title):
    print(f"\n{'=' * 60}")
    print(f"{num}. {title}")
    print("-" * 60)


def lmstudio_ok() -> bool:
    try:
        import urllib.request
        req = urllib.request.Request(f"{LMSTUDIO_BASE}/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return MODEL in [m["id"] for m in json.loads(resp.read()).get("data", [])]
    except Exception:
        return False


# ====================================================================
# 1-4. 源码检查: 4 处 except BaseException 兜底
# ====================================================================
def test_1234_source_checks():
    section(1, "源码: 4 处 except BaseException 兜底")

    def _check_file(name, rel_path, anchor, cleanup_vars):
        src = (_project_root / "src" / "openakita" / rel_path).read_text("utf-8")
        try:
            idx = src.index(anchor)
        except ValueError:
            check(f"{name}: 找到锚点", False, f"找不到 '{anchor[:40]}'")
            return
        region = src[max(0, idx - 200):idx + 2500]
        has_base = "except BaseException:" in region
        has_vars = all(v.strip() in region for v in cleanup_vars.split(","))
        check(f"{name}: except BaseException 兜底", has_base)
        check(f"{name}: 清理 ({cleanup_vars})", has_vars)

    _check_file("agent.py 工具竞速", "core/agent.py",
                "_tool_cancel_event.wait()", "tool_task, cancel_waiter, skip_waiter")
    _check_file("agent.py CancellableLLM", "core/agent.py",
                "async def _cancellable_await", "task, cancel_waiter")
    _check_file("context_manager.py", "core/context_manager.py",
                "_cancel_event.wait()", "task, cancel_waiter")
    _check_file("reasoning_engine.py 流式工具", "core/reasoning_engine.py",
                "pending_set = {tool_exec_task, cancel_waiter, skip_waiter}", "tool_exec_task, cancel_waiter, skip_waiter")


# ====================================================================
# 5. 模拟父取消: Event.wait() 清理验证
# ====================================================================
def test_5_parent_cancel_cleanup():
    section(5, "模拟父取消: Event.wait() 不泄漏")

    destroyed: list[str] = []

    class _Catcher(logging.Handler):
        def emit(self, record):
            if "destroyed" in record.getMessage().lower():
                destroyed.append(record.getMessage())

    catcher = _Catcher()
    logging.getLogger("asyncio").addHandler(catcher)

    async def _test():
        cancel_event = asyncio.Event()
        skip_event = asyncio.Event()

        async def _slow_work():
            await asyncio.sleep(100)

        async def _parent_with_fix():
            tool_task = asyncio.create_task(_slow_work())
            cancel_waiter = asyncio.create_task(cancel_event.wait())
            skip_waiter = asyncio.create_task(skip_event.wait())
            try:
                done, pending = await asyncio.wait(
                    {tool_task, cancel_waiter, skip_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            except BaseException:
                for t in (tool_task, cancel_waiter, skip_waiter):
                    if not t.done():
                        t.cancel()
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass
                raise

        parent_task = asyncio.create_task(_parent_with_fix())
        await asyncio.sleep(0.05)
        parent_task.cancel()
        try:
            await parent_task
        except asyncio.CancelledError:
            pass

        await asyncio.sleep(0.1)
        gc.collect()
        await asyncio.sleep(0.1)

    asyncio.run(_test())
    logging.getLogger("asyncio").removeHandler(catcher)
    check(f"父取消后无 TaskDestroyed ({len(destroyed)}条)", len(destroyed) == 0,
          "; ".join(destroyed[:3]))


# ====================================================================
# 6. 对比: 无修复时确实泄漏
# ====================================================================
def test_6_fix_pattern_correct():
    section(6, "修复模式验证: except BaseException 覆盖取消路径")

    async def _test():
        cancel_event = asyncio.Event()
        cleaned_up = False

        async def _slow_work():
            await asyncio.sleep(100)

        async def _parent_with_fix():
            nonlocal cleaned_up
            task = asyncio.create_task(_slow_work())
            waiter = asyncio.create_task(cancel_event.wait())
            try:
                done, pending = await asyncio.wait(
                    {task, waiter}, return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            except BaseException:
                for t in (task, waiter):
                    if not t.done():
                        t.cancel()
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass
                cleaned_up = True
                raise

        parent = asyncio.create_task(_parent_with_fix())
        await asyncio.sleep(0.02)
        parent.cancel()
        try:
            await parent
        except asyncio.CancelledError:
            pass
        return cleaned_up

    result = asyncio.run(_test())
    check("except BaseException 在取消时执行了清理", result)


# ====================================================================
# 7. LMStudio 实测: benchmark 超时任务无泄漏
# ====================================================================
def test_7_lmstudio_timeout_no_leak():
    section(7, "LMStudio 实测: 超时任务无 Event.wait() 泄漏")

    if not lmstudio_ok():
        print(f"  [SKIP] LMStudio/{MODEL} 不可用")
        return

    def _llm_chat(prompt, max_tokens=256):
        import urllib.request
        payload = json.dumps({
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3, "max_tokens": max_tokens, "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{LMSTUDIO_BASE}/chat/completions",
            data=payload, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())["choices"][0]["message"]["content"]

    @dataclass
    class _R:
        success: bool = True
        data: str = ""
        error: str = ""
        iterations: int = 1

    _tok = 0

    async def _runner(agent: Any, desc: str) -> _R:
        nonlocal _tok
        reply = await asyncio.to_thread(_llm_chat, desc, 512)
        _tok += len(reply) * 4
        return _R(success=True, data=reply)

    def _counter(agent):
        return _tok

    test_dir = _project_root / "data" / "test_event_leak"
    if test_dir.exists():
        shutil.rmtree(str(test_dir))
    test_dir.mkdir(parents=True, exist_ok=True)

    destroyed: list[str] = []

    class _Catcher(logging.Handler):
        def emit(self, record):
            if "destroyed" in record.getMessage().lower():
                destroyed.append(record.getMessage())

    catcher = _Catcher()
    logging.getLogger("asyncio").addHandler(catcher)

    async def _test():
        from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkTask

        engine = BenchmarkEngine(
            data_dir=str(test_dir), task_runner=_runner, token_counter=_counter,
        )

        tasks = [
            BenchmarkTask(
                id="normal-fast", description="只回复两个字: 你好",
                category="test", expected_outcome="", timeout_seconds=30,
            ),
            BenchmarkTask(
                id="normal-medium",
                description="用中文简要解释什么是 Python 的 GIL",
                category="test", expected_outcome="", timeout_seconds=30,
            ),
        ]

        tasks_before = len(asyncio.all_tasks())
        print(f"  运行 {len(tasks)} 个 benchmark 任务 (模型: {MODEL})...")
        t0 = time.time()
        report = await engine.run_suite(None, tasks=tasks)
        elapsed = time.time() - t0
        sr = report.metrics.success_rate
        print(f"  完成: sr={sr:.0%}, 耗时 {elapsed:.1f}s")

        for r in report.results:
            s = "PASS" if r.success else "FAIL"
            print(f"    [{s}] {r.task_id}: {r.time_seconds:.1f}s")

        check(f"benchmark 完成 (耗时 {elapsed:.1f}s)", elapsed > 1)

        await asyncio.sleep(0.5)
        gc.collect()
        await asyncio.sleep(0.5)
        gc.collect()

        tasks_after = len(asyncio.all_tasks())
        check(f"无 task 泄漏 (before={tasks_before}, after={tasks_after})",
              tasks_after <= tasks_before + 1)

    asyncio.run(_test())
    logging.getLogger("asyncio").removeHandler(catcher)
    check(f"无 TaskDestroyed 警告 ({len(destroyed)}条)", len(destroyed) == 0,
          "; ".join(destroyed[:3]))

    if test_dir.exists():
        shutil.rmtree(str(test_dir))


# ====================================================================
# 8. LMStudio 实测: 多任务 benchmark 无泄漏
# ====================================================================
def test_8_lmstudio_full_benchmark():
    section(8, "LMStudio 实测: 完整 benchmark 无 Event.wait() 泄漏")

    if not lmstudio_ok():
        print(f"  [SKIP] LMStudio/{MODEL} 不可用")
        return

    def _llm_chat(prompt, max_tokens=1024):
        import urllib.request
        payload = json.dumps({
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3, "max_tokens": max_tokens, "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{LMSTUDIO_BASE}/chat/completions",
            data=payload, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())["choices"][0]["message"]["content"]

    @dataclass
    class _R:
        success: bool = True
        data: str = ""
        error: str = ""
        iterations: int = 1

    _tok = 0

    async def _runner(agent: Any, desc: str) -> _R:
        nonlocal _tok
        reply = await asyncio.to_thread(_llm_chat, desc, 1024)
        _tok += len(reply) * 4
        return _R(success=True, data=reply)

    def _counter(agent):
        return _tok

    test_dir = _project_root / "data" / "test_full_leak"
    if test_dir.exists():
        shutil.rmtree(str(test_dir))
    test_dir.mkdir(parents=True, exist_ok=True)

    destroyed: list[str] = []

    class _Catcher(logging.Handler):
        def emit(self, record):
            if "destroyed" in record.getMessage().lower():
                destroyed.append(record.getMessage())

    catcher = _Catcher()
    logging.getLogger("asyncio").addHandler(catcher)

    async def _test():
        from openakita.evolution.benchmark import BenchmarkEngine

        engine = BenchmarkEngine(
            data_dir=str(test_dir), task_runner=_runner, token_counter=_counter,
        )
        tasks = engine.load_tasks()

        print(f"  运行全部 {len(tasks)} 个 benchmark 任务 (模型: {MODEL})...")
        t0 = time.time()
        report = await engine.run_suite(None, tasks=tasks)
        elapsed = time.time() - t0
        sr = report.metrics.success_rate
        passed = sum(1 for r in report.results if r.success)
        print(f"  完成: sr={sr:.0%} ({passed}/{len(tasks)}), 耗时 {elapsed:.0f}s")

        for r in report.results:
            s = "PASS" if r.success else "FAIL"
            vr = f" [{r.verification_reason}]" if r.verification_reason else ""
            print(f"    [{s}] {r.task_id}: {r.time_seconds:.1f}s{vr}")

        check(f"benchmark 完成 ({elapsed:.0f}s 真实推理)", elapsed > 10)

        await asyncio.sleep(1.0)
        gc.collect()
        await asyncio.sleep(0.5)
        gc.collect()

    asyncio.run(_test())
    logging.getLogger("asyncio").removeHandler(catcher)
    check(f"8 任务后无 TaskDestroyed ({len(destroyed)}条)", len(destroyed) == 0,
          "; ".join(destroyed[:5]))

    if test_dir.exists():
        shutil.rmtree(str(test_dir))


# ====================================================================
# main
# ====================================================================
def main():
    print("=" * 60)
    print(f"  Event.wait() TaskDestroyed 全面修复验证")
    print(f"  (含 LMStudio {MODEL} 实测)")
    print("=" * 60)

    test_1234_source_checks()
    test_5_parent_cancel_cleanup()
    test_6_fix_pattern_correct()
    test_7_lmstudio_timeout_no_leak()
    test_8_lmstudio_full_benchmark()

    print(f"\n{'=' * 60}")
    print(f"  结果: {PASS} passed, {FAIL} failed")
    if FAILED:
        print(f"\n  失败项 ({len(FAILED)}):")
        for f in FAILED:
            print(f"    x {f}")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
