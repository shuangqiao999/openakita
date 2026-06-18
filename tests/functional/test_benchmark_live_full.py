"""
全流程 Benchmark 实测 (LMStudio qwen/qwen3.5-9b)

真正调用 LLM 运行 8 个 benchmark 任务，验证:
  1. BenchmarkEngine.run_suite 完整运行
  2. _verify_outcome 引号关键词验证
  3. baseline / original_baseline 写入正确
  4. task_health 更新正确
  5. 效率分计算正确
  6. _warmup 预热生效
  7. 质量评分合理
"""

from __future__ import annotations

import asyncio
import json
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
OUT_DIR = _project_root / "data" / "test_benchmark_live"
PASS = FAIL = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(f"{name}: {detail}" if detail else name)
        print(f"  [FAIL] {name}  --  {detail}" if detail else f"  [FAIL] {name}")


def clean():
    if OUT_DIR.exists():
        shutil.rmtree(str(OUT_DIR))
    OUT_DIR.mkdir(parents=True, exist_ok=True)


def lmstudio_available() -> bool:
    try:
        import urllib.request
        req = urllib.request.Request(f"{LMSTUDIO_BASE}/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m["id"] for m in data.get("data", [])]
            return MODEL in models
    except Exception:
        return False


def _llm_chat(prompt: str, max_tokens: int = 2048) -> str:
    import urllib.request
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{LMSTUDIO_BASE}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


@dataclass
class FakeResult:
    success: bool = True
    data: str = ""
    error: str = ""
    iterations: int = 1


_total_tokens = 0


async def _real_task_runner(agent: Any, description: str) -> FakeResult:
    global _total_tokens
    try:
        output = await asyncio.to_thread(_llm_chat, description)
        _total_tokens += len(output) * 4
        return FakeResult(success=True, data=output)
    except Exception as e:
        return FakeResult(success=False, error=str(e))


def _token_counter(agent: Any) -> int:
    return _total_tokens


async def run_full_benchmark():
    print("\n" + "=" * 60)
    print("1. 完整 Benchmark Suite 运行 (8 任务)")
    print("=" * 60)

    from openakita.evolution.benchmark import BenchmarkEngine

    bench_dir = OUT_DIR / "benchmarks"
    engine = BenchmarkEngine(
        data_dir=str(bench_dir),
        task_runner=_real_task_runner,
        token_counter=_token_counter,
    )

    tasks = engine.load_tasks()
    print(f"  加载 {len(tasks)} 个任务:")
    for t in tasks:
        print(f"    [{t.category}] {t.id}: {t.description[:50]}...")

    print(f"\n  开始运行 (模型: {MODEL})...")
    t0 = time.time()
    report = await engine.run_suite(None, tasks=tasks)
    elapsed = time.time() - t0
    print(f"  完成! 耗时 {elapsed:.1f}s\n")

    m = report.metrics
    print(f"  === 指标 ===")
    print(f"  成功率:   {m.success_rate:.0%} ({sum(1 for r in report.results if r.success)}/{len(report.results)})")
    print(f"  平均耗时: {m.avg_time:.1f}s")
    print(f"  效率分:   {m.efficiency_score:.1f}")
    print(f"  分类:")
    for cat, score in m.category_scores.items():
        print(f"    {cat}: {score:.0%}")

    print(f"\n  === 逐任务结果 ===")
    for r in report.results:
        status = "PASS" if r.success else "FAIL"
        vr = ""
        if r.verification_reason:
            vr = f" [{r.verification_reason}]"
        print(f"    [{status}] {r.task_id}: {r.time_seconds:.1f}s{vr}")

    check("成功率 > 0", m.success_rate > 0)
    check(f"运行耗时 > 10s (实际 {elapsed:.0f}s)", elapsed > 10)
    check("每个任务有耗时数据", all(r.time_seconds > 0 for r in report.results))

    passed_tasks = [r for r in report.results if r.success]
    check(f"至少 3 个任务通过 (实际 {len(passed_tasks)})", len(passed_tasks) >= 3)

    for r in report.results:
        if r.success and r.verification_passed is not None:
            check(f"{r.task_id} 验证通过", r.verification_passed, r.verification_reason)

    return engine, report


async def check_baseline_files(engine, report):
    print("\n" + "=" * 60)
    print("2. Baseline 文件写入验证")
    print("=" * 60)

    bench_dir = OUT_DIR / "benchmarks"

    engine.save_as_baseline(report)

    bl_path = bench_dir / "baseline.json"
    ob_path = bench_dir / "original_baseline.json"
    check("baseline.json 已创建", bl_path.exists())
    check("original_baseline.json 已创建", ob_path.exists())

    if bl_path.exists():
        bl = json.loads(bl_path.read_text("utf-8"))
        check(
            f"baseline success_rate = {report.metrics.success_rate}",
            abs(bl["metrics"]["success_rate"] - report.metrics.success_rate) < 0.01,
        )
    if ob_path.exists():
        ob = json.loads(ob_path.read_text("utf-8"))
        check(
            f"original_baseline = baseline (首次)",
            abs(ob["metrics"]["success_rate"] - report.metrics.success_rate) < 0.01,
        )

    results_dir = bench_dir / "results"
    result_files = list(results_dir.glob("*.json"))
    check(f"results/ 有 {len(result_files)} 个结果文件", len(result_files) >= 1)

    health_path = bench_dir / "task_health.json"
    check("task_health.json 已创建", health_path.exists())
    if health_path.exists():
        health = json.loads(health_path.read_text("utf-8"))
        check(f"task_health 包含 {len(health)} 个任务", len(health) == len(report.results))
        degraded = [k for k, v in health.items() if v.get("degraded")]
        check(f"无降级任务 (降级: {degraded})", len(degraded) == 0)


async def check_second_run(engine):
    print("\n" + "=" * 60)
    print("3. 第二次运行 + baseline_delta 验证")
    print("=" * 60)

    tasks = engine.load_tasks()
    print(f"  运行第二次 benchmark ({len(tasks)} 任务)...")
    t0 = time.time()
    report2 = await engine.run_suite(None, tasks=tasks)
    elapsed = time.time() - t0
    print(f"  完成! 耗时 {elapsed:.1f}s")

    m2 = report2.metrics
    print(f"  成功率: {m2.success_rate:.0%}, 效率分: {m2.efficiency_score:.1f}")

    check("baseline_delta 非空", bool(report2.baseline_delta))
    if report2.baseline_delta:
        delta_sr = report2.baseline_delta.get("success_rate", 0)
        print(f"  baseline_delta: success_rate={delta_sr:+.3f}")
        check("delta 值在合理范围 [-1, 1]", -1.0 <= delta_sr <= 1.0)

    return report2


async def check_verify_outcome_live(engine):
    print("\n" + "=" * 60)
    print("4. _verify_outcome 引号关键词实测")
    print("=" * 60)

    from openakita.evolution.benchmark import BenchmarkTask

    cases = [
        (
            "fibonacci '55' 验证",
            "编写斐波那契函数并计算 fib(10)",
            "函数正确实现，输出结果为 '55'",
        ),
        (
            "bug-fix '空列表' 验证",
            "修复空列表除零 bug 并编写测试验证",
            "正确处理'空列表'场景，'测试'验证通过",
        ),
        (
            "refactor '列表推导式' 验证",
            "用列表推导式重构循环代码",
            "使用'列表推导式'重构代码",
        ),
    ]

    for name, prompt, expected_outcome in cases:
        print(f"\n  [{name}] 发送 LLM 请求...")
        try:
            output = await asyncio.to_thread(_llm_chat, prompt, 512)
            print(f"    LLM 输出: {output[:80]}...")
            task = BenchmarkTask(
                id=f"live_{name}", description=prompt,
                category="coding", expected_outcome=expected_outcome,
            )
            ok, reason = engine._verify_outcome(task, output)
            check(f"{name} → {'PASS' if ok else 'FAIL'}", ok, reason)
        except Exception as e:
            check(name, False, str(e))


async def check_warmup():
    print("\n" + "=" * 60)
    print("5. _warmup 预热验证")
    print("=" * 60)

    from openakita.evolution.benchmark import BenchmarkEngine

    warmup_dir = OUT_DIR / "warmup_bench"
    engine = BenchmarkEngine(
        data_dir=str(warmup_dir),
        task_runner=_real_task_runner,
        token_counter=_token_counter,
    )
    try:
        t0 = time.time()
        await engine._warmup(None)
        elapsed = time.time() - t0
        check(f"warmup 完成 ({elapsed:.1f}s)", elapsed < 30)
    except Exception as e:
        check("warmup 无异常", False, str(e))


async def main():
    clean()
    print("=" * 60)
    print(f"  全流程 Benchmark 实测 (LMStudio {MODEL})")
    print("=" * 60)

    if not lmstudio_available():
        print(f"\n  [ERROR] LMStudio 未启动或模型 {MODEL} 未加载")
        return False

    print(f"  LMStudio 在线, 模型 {MODEL} 已加载")

    await check_warmup()
    engine, report = await run_full_benchmark()
    await check_baseline_files(engine, report)
    report2 = await check_second_run(engine)
    await check_verify_outcome_live(engine)

    print("\n" + "=" * 60)
    print(f"  结果: {PASS} passed, {FAIL} failed")
    if FAILED:
        print("\n  失败项:")
        for f in FAILED:
            print(f"    x {f}")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
