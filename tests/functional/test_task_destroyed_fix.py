"""asyncio TaskDestroyed 修复验证 (含 LMStudio 实测)"""
from __future__ import annotations

import asyncio
import gc
import inspect
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


def lmstudio_available() -> bool:
    try:
        import urllib.request
        req = urllib.request.Request(f"{LMSTUDIO_BASE}/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return MODEL in [m["id"] for m in data.get("data", [])]
    except Exception:
        return False


def _llm_chat(prompt, max_tokens=256):
    import urllib.request
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0, "max_tokens": max_tokens, "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{LMSTUDIO_BASE}/chat/completions",
        data=payload, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]


# ====================================================================
# 1. 源码: reasoning_engine cancel/hb 清理
# ====================================================================
def test_1_source_cleanup():
    print("\n" + "=" * 60)
    print("1. reasoning_engine cancel/hb 清理 (源码)")

    src = (_project_root / "src" / "openakita" / "core" / "reasoning_engine.py").read_text("utf-8")
    idx = src.index("cancel_task = asyncio.create_task(_cancel_watcher())")
    region = src[idx:idx + 2000]

    check("不再裸 cancel hb_task", "hb_task.cancel()\n" not in region)
    check("不再裸 cancel cancel_task", "cancel_task.cancel()\n" not in region)
    check("for _t in 统一清理", "for _t in (hb_task, cancel_task)" in region)
    check("await _t", "await _t" in region)
    check("CancelledError 捕获", "asyncio.CancelledError" in region)


# ====================================================================
# 2. 源码: executor 无重复 warmup
# ====================================================================
def test_2_no_dup_warmup():
    print("\n" + "=" * 60)
    print("2. executor 无重复 warmup (源码)")

    from openakita.scheduler.executor import TaskExecutor
    src = inspect.getsource(TaskExecutor._system_benchmark_evolve_inner)
    check("executor warmup 调用 = 0", src.count("_warmup") == 0)

    from openakita.evolution.benchmark import BenchmarkEngine
    check("run_suite 保留 warmup", "_warmup" in inspect.getsource(BenchmarkEngine.run_suite))


# ====================================================================
# 3. 模拟: Event.wait() 正确清理
# ====================================================================
def test_3_simulation():
    print("\n" + "=" * 60)
    print("3. Event.wait() 模拟清理验证")

    async def _test():
        event = asyncio.Event()
        queue = asyncio.Queue()

        async def _cancel_watcher():
            try:
                await event.wait()
            except asyncio.CancelledError:
                pass

        async def _heartbeat():
            try:
                while True:
                    await asyncio.sleep(0.01)
                    await queue.put(("hb", None))
            except asyncio.CancelledError:
                pass

        async def _work():
            await asyncio.sleep(0.05)
            await queue.put(("done", None))

        reason = asyncio.create_task(_work())
        hb = asyncio.create_task(_heartbeat())
        cancel = asyncio.create_task(_cancel_watcher())

        try:
            while True:
                typ, _ = await queue.get()
                if typ == "done":
                    break
        finally:
            for _t in (hb, cancel):
                _t.cancel()
                try:
                    await _t
                except (asyncio.CancelledError, Exception):
                    pass

        return hb.done() and cancel.done() and reason.done()

    check("所有 task 完成 (非 pending)", asyncio.run(_test()))


# ====================================================================
# 4. LMStudio 实测: LLM 调用无 Task 泄漏
# ====================================================================
def test_4_llm_no_leak():
    print("\n" + "=" * 60)
    print("4. LMStudio 实测: LLM 调用无 Task 泄漏")

    if not lmstudio_available():
        print(f"  [SKIP] LMStudio/{MODEL} 不可用")
        return

    destroyed: list[str] = []

    class _Catcher(logging.Handler):
        def emit(self, record):
            if "destroyed" in record.getMessage().lower():
                destroyed.append(record.getMessage())

    catcher = _Catcher()
    logging.getLogger("asyncio").addHandler(catcher)

    async def _test():
        tasks_before = len(asyncio.all_tasks())
        reply = await asyncio.to_thread(_llm_chat, "只回复两个字: 你好", 20)
        print(f"  LLM 回复: {reply}")
        check("LLM 返回有效内容", len(reply) > 0)

        await asyncio.sleep(0.2)
        gc.collect()
        await asyncio.sleep(0.1)

        tasks_after = len(asyncio.all_tasks())
        check(f"无 task 泄漏 (before={tasks_before}, after={tasks_after})",
              tasks_after <= tasks_before + 1)

    asyncio.run(_test())
    logging.getLogger("asyncio").removeHandler(catcher)
    check(f"无 TaskDestroyed 警告 ({len(destroyed)}条)", len(destroyed) == 0,
          "; ".join(destroyed[:3]))


# ====================================================================
# 5. Benchmark 单任务实测: 无 Event.wait() 泄漏
# ====================================================================
def test_5_benchmark_no_leak():
    print("\n" + "=" * 60)
    print("5. Benchmark 单任务实测: 无 Event.wait() 泄漏")

    if not lmstudio_available():
        print(f"  [SKIP] LMStudio/{MODEL} 不可用")
        return

    test_dir = _project_root / "data" / "test_task_leak"
    if test_dir.exists():
        shutil.rmtree(str(test_dir))
    test_dir.mkdir(parents=True, exist_ok=True)

    @dataclass
    class _R:
        success: bool = True
        data: str = ""
        error: str = ""
        iterations: int = 1

    _tok = 0

    async def _runner(agent: Any, desc: str) -> _R:
        nonlocal _tok
        reply = await asyncio.to_thread(_llm_chat, desc, 256)
        _tok += len(reply) * 4
        return _R(success=True, data=reply)

    def _counter(agent):
        return _tok

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
        tasks = [BenchmarkTask(
            id="leak-test", description="用中文回答: 1+1等于几? 只回答数字。",
            category="test", expected_outcome="", timeout_seconds=30,
        )]

        tasks_before = len(asyncio.all_tasks())
        print(f"  运行 1 个 benchmark 任务...")
        t0 = time.time()
        report = await engine.run_suite(None, tasks=tasks)
        elapsed = time.time() - t0
        sr = report.metrics.success_rate
        print(f"  完成: sr={sr:.0%}, 耗时 {elapsed:.1f}s")
        check(f"任务完成 (耗时 {elapsed:.1f}s)", elapsed > 0.5)

        await asyncio.sleep(0.3)
        gc.collect()
        await asyncio.sleep(0.2)

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
# main
# ====================================================================
def main():
    print("=" * 60)
    print(f"  asyncio TaskDestroyed 修复验证 (含 LMStudio 实测)")
    print("=" * 60)

    test_1_source_cleanup()
    test_2_no_dup_warmup()
    test_3_simulation()
    test_4_llm_no_leak()
    test_5_benchmark_no_leak()

    print("\n" + "=" * 60)
    print(f"  结果: {PASS} passed, {FAIL} failed")
    if FAILED:
        for f in FAILED:
            print(f"    x {f}")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
