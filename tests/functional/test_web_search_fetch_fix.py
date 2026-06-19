"""web_search + web_fetch 修复验证 (含 LMStudio 实测)"""
from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import sys
import time
from pathlib import Path

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


def lmstudio_ok():
    try:
        import urllib.request
        with urllib.request.urlopen(f"{LMSTUDIO_BASE}/models", timeout=5) as r:
            return MODEL in [m["id"] for m in json.loads(r.read()).get("data", [])]
    except Exception:
        return False


# ====================================================================
# 1. web_search 源码: pending cancel 加 await + except BaseException
# ====================================================================
def test_1_web_search_source():
    section(1, "web_search 源码修复检查")

    src = (_project_root / "src" / "openakita" / "tools" / "handlers" / "web_search.py").read_text("utf-8")
    idx = src.index("tier_tasks = [")
    region = src[idx:idx + 1500]

    check("pending cancel 后有 await t", "await t" in region.split("for t in pending")[1][:200]
          if "for t in pending" in region else False)
    check("except BaseException 兜底", "except BaseException:" in region)
    check("tier_tasks 清理", "for t in tier_tasks:" in region)
    check("raise 传播异常", region.count("raise") >= 1)


# ====================================================================
# 2. tool_executor 源码: repr 错误信息
# ====================================================================
def test_2_tool_executor_source():
    section(2, "tool_executor 空错误信息修复")

    src = (_project_root / "src" / "openakita" / "core" / "tool_executor.py").read_text("utf-8")

    check("使用 {e!r} repr 格式", "{e!r}" in src or "e!r" in src)
    check("不再使用裸 {e} 格式",
          "batch execution error: {tool_name}: {e}" not in src
          or "{e!r}" in src.split("batch execution error")[1][:50])

    err1 = ConnectionError()
    err2 = TimeoutError()
    check(f"ConnectionError repr 非空: {err1!r}", len(f"{err1!r}") > 0)
    check(f"TimeoutError repr 非空: {err2!r}", len(f"{err2!r}") > 0)


# ====================================================================
# 3. 模拟 web_search tier 竞速: cancel 后无异常泄漏
# ====================================================================
def test_3_tier_race_simulation():
    section(3, "模拟 tier 竞速: cancel 后无 Task 异常泄漏")

    destroyed: list[str] = []
    unretreived: list[str] = []

    class _Catcher(logging.Handler):
        def emit(self, record):
            msg = record.getMessage().lower()
            if "destroyed" in msg:
                destroyed.append(record.getMessage())
            if "never retrieved" in msg:
                unretreived.append(record.getMessage())

    catcher = _Catcher()
    logging.getLogger("asyncio").addHandler(catcher)

    async def _test():
        async def _fast_tier():
            await asyncio.sleep(0.02)
            return ["result1", "result2"]

        async def _slow_tier_timeout():
            await asyncio.sleep(100)
            raise TimeoutError("slow tier timed out")

        async def _slow_tier_error():
            await asyncio.sleep(100)
            raise ConnectionError("connection failed")

        tier_tasks = [
            asyncio.create_task(_fast_tier()),
            asyncio.create_task(_slow_tier_timeout()),
            asyncio.create_task(_slow_tier_error()),
        ]

        try:
            done, pending = await asyncio.wait(
                tier_tasks, return_when=asyncio.FIRST_COMPLETED, timeout=5.0,
            )
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        except BaseException:
            for t in tier_tasks:
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            raise

        results = []
        for t in done:
            try:
                r = t.result()
                if r:
                    results.extend(r)
            except Exception:
                pass

        check(f"fast_tier 结果返回 ({len(results)} 条)", len(results) == 2)

        all_done = all(t.done() for t in tier_tasks)
        check("所有 tier task 已完成", all_done)

        await asyncio.sleep(0.2)
        gc.collect()
        await asyncio.sleep(0.1)

    asyncio.run(_test())
    logging.getLogger("asyncio").removeHandler(catcher)

    check(f"无 TaskDestroyed ({len(destroyed)}条)", len(destroyed) == 0,
          "; ".join(destroyed[:3]))
    check(f"无 exception never retrieved ({len(unretreived)}条)", len(unretreived) == 0,
          "; ".join(unretreived[:3]))


# ====================================================================
# 4. 模拟父取消: tier 竞速的 except BaseException 生效
# ====================================================================
def test_4_tier_parent_cancel():
    section(4, "模拟父取消: tier 竞速清理生效")

    destroyed: list[str] = []

    class _Catcher(logging.Handler):
        def emit(self, record):
            if "destroyed" in record.getMessage().lower():
                destroyed.append(record.getMessage())

    catcher = _Catcher()
    logging.getLogger("asyncio").addHandler(catcher)

    async def _test():
        cleaned_up = False

        async def _slow():
            await asyncio.sleep(100)

        async def _parent():
            nonlocal cleaned_up
            tier_tasks = [
                asyncio.create_task(_slow()),
                asyncio.create_task(_slow()),
                asyncio.create_task(_slow()),
            ]
            try:
                done, pending = await asyncio.wait(
                    tier_tasks, return_when=asyncio.FIRST_COMPLETED, timeout=60.0,
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            except BaseException:
                for t in tier_tasks:
                    if not t.done():
                        t.cancel()
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass
                cleaned_up = True
                raise

        parent = asyncio.create_task(_parent())
        await asyncio.sleep(0.05)
        parent.cancel()
        try:
            await parent
        except asyncio.CancelledError:
            pass

        await asyncio.sleep(0.2)
        gc.collect()
        await asyncio.sleep(0.1)
        return cleaned_up

    result = asyncio.run(_test())
    logging.getLogger("asyncio").removeHandler(catcher)

    check("except BaseException 触发清理", result)
    check(f"父取消后无 TaskDestroyed ({len(destroyed)}条)", len(destroyed) == 0)


# ====================================================================
# 5. LMStudio 实测: benchmark + web_search 场景无泄漏
# ====================================================================
def test_5_lmstudio_benchmark():
    section(5, "LMStudio 实测: benchmark 无 Task 泄漏")

    if not lmstudio_ok():
        print(f"  [SKIP] LMStudio/{MODEL} 不可用")
        return

    from dataclasses import dataclass
    from typing import Any
    import shutil

    def _llm(prompt, max_tokens=512):
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
        reply = await asyncio.to_thread(_llm, desc)
        _tok += len(reply) * 4
        return _R(success=True, data=reply)

    test_dir = _project_root / "data" / "test_websearch_fix"
    if test_dir.exists():
        shutil.rmtree(str(test_dir))
    test_dir.mkdir(parents=True, exist_ok=True)

    destroyed: list[str] = []
    unretrieved: list[str] = []

    class _Catcher(logging.Handler):
        def emit(self, record):
            msg = record.getMessage().lower()
            if "destroyed" in msg:
                destroyed.append(record.getMessage())
            if "never retrieved" in msg:
                unretrieved.append(record.getMessage())

    catcher = _Catcher()
    logging.getLogger("asyncio").addHandler(catcher)

    async def _test():
        from openakita.evolution.benchmark import BenchmarkEngine

        engine = BenchmarkEngine(
            data_dir=str(test_dir), task_runner=_runner,
            token_counter=lambda a: _tok,
        )
        tasks = engine.load_tasks()
        print(f"  运行 {len(tasks)} 个 benchmark 任务...")
        t0 = time.time()
        report = await engine.run_suite(None, tasks=tasks)
        elapsed = time.time() - t0
        passed = sum(1 for r in report.results if r.success)
        print(f"  完成: {passed}/{len(tasks)}, 耗时 {elapsed:.0f}s")

        await asyncio.sleep(1.0)
        gc.collect()
        await asyncio.sleep(0.5)

    asyncio.run(_test())
    logging.getLogger("asyncio").removeHandler(catcher)

    check(f"无 TaskDestroyed ({len(destroyed)}条)", len(destroyed) == 0,
          "; ".join(destroyed[:3]))
    check(f"无 exception never retrieved ({len(unretrieved)}条)", len(unretrieved) == 0,
          "; ".join(unretrieved[:3]))

    if test_dir.exists():
        shutil.rmtree(str(test_dir))


# ====================================================================
# main
# ====================================================================
def main():
    print("=" * 60)
    print(f"  web_search + web_fetch 修复验证")
    print("=" * 60)

    test_1_web_search_source()
    test_2_tool_executor_source()
    test_3_tier_race_simulation()
    test_4_tier_parent_cancel()
    test_5_lmstudio_benchmark()

    print(f"\n{'=' * 60}")
    print(f"  结果: {PASS} passed, {FAIL} failed")
    if FAILED:
        for f in FAILED:
            print(f"    x {f}")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
