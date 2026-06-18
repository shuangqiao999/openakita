"""
自进化数据审查修复验证测试

验证:
  P1. 实验 keep 后更新 baseline
  P2. code-bug-fix expected_outcome 引号关键词验证稳定性
  N2. code-fibonacci / code-refactor expected_outcome 改进
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
from pathlib import Path

_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-needed")
os.environ.setdefault("DEFAULT_MODEL", "local-test")

OUT_DIR = _project_root / "data" / "test_evolution_audit"
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


def clean():
    if OUT_DIR.exists():
        shutil.rmtree(str(OUT_DIR))
    OUT_DIR.mkdir(parents=True, exist_ok=True)


def test_p1_baseline_update_in_code():
    print("\n" + "=" * 60)
    print("P1. 实验 keep 后更新 baseline (代码检查)")

    from openakita.evolution.experiment_loop import ExperimentLoop

    src_exp = inspect.getsource(ExperimentLoop._run_experiment)
    keep_idx = src_exp.index('action="keep"')
    baseline_call = "save_as_baseline"
    check(
        "_run_experiment keep 后调用 save_as_baseline",
        baseline_call in src_exp[:keep_idx],
        "save_as_baseline 应在 return keep 之前",
    )

    src_env = inspect.getsource(ExperimentLoop._run_env_experiment)
    keep_idx_env = src_env.index('action="keep"')
    check(
        "_run_env_experiment keep 后调用 save_as_baseline",
        baseline_call in src_env[:keep_idx_env],
    )

    from openakita.evolution import research_org

    src_research = inspect.getsource(research_org)
    adoption_idx = src_research.index("Prompt 变更已采纳")
    save_idx = src_research.index("save_as_baseline", adoption_idx)
    check(
        "research_org 采纳后调用 save_as_baseline",
        save_idx > adoption_idx,
    )


def test_p1_baseline_update_functional():
    print("\n" + "=" * 60)
    print("P1. 实验 keep 后更新 baseline (功能测试)")

    from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkMetrics, BenchmarkReport

    bd = OUT_DIR / "bench_p1"
    engine = BenchmarkEngine(data_dir=str(bd))

    baseline_before = engine._load_latest_baseline()
    check("初始无 baseline", baseline_before is None)

    report = BenchmarkReport(
        timestamp="2026-06-18T16:00:00",
        metrics=BenchmarkMetrics(
            success_rate=0.9,
            avg_tokens=100000,
            avg_time=10.0,
            efficiency_score=80.0,
        ),
    )
    engine.save_as_baseline(report)

    baseline_after = engine._load_latest_baseline()
    check("save_as_baseline 后有 baseline", baseline_after is not None)
    if baseline_after:
        check("baseline success_rate = 0.9", abs(baseline_after.success_rate - 0.9) < 0.01)

    report2 = BenchmarkReport(
        timestamp="2026-06-18T17:00:00",
        metrics=BenchmarkMetrics(
            success_rate=1.0,
            avg_tokens=80000,
            avg_time=8.0,
            efficiency_score=90.0,
        ),
    )
    engine.save_as_baseline(report2)
    baseline_updated = engine._load_latest_baseline()
    check(
        "二次 save_as_baseline 更新到 1.0",
        baseline_updated is not None and abs(baseline_updated.success_rate - 1.0) < 0.01,
    )

    orig = bd / "original_baseline.json"
    check("original_baseline 仍为首次 (0.9)", orig.exists())
    if orig.exists():
        data = json.loads(orig.read_text(encoding="utf-8"))
        check(
            "original_baseline 未被覆盖",
            abs(data["metrics"]["success_rate"] - 0.9) < 0.01,
        )


def test_p2_verify_outcome_stability():
    print("\n" + "=" * 60)
    print("P2. expected_outcome 验证稳定性")

    from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkTask

    engine = BenchmarkEngine(data_dir=str(OUT_DIR / "bench_p2"))

    bug_fix_task = BenchmarkTask(
        id="code-bug-fix",
        description="fix bug",
        category="coding",
        expected_outcome="正确处理'空列表'场景，'测试'验证通过",
    )
    outputs = [
        "修复了空列表导致的除零错误，添加了边界检查。运行测试全部通过。",
        "函数现在处理空列表时返回0。编写了3个测试用例验证通过。",
        "Fixed: when lst is empty, return 0. Added test for 空列表 case. 测试 passed.",
        "def avg(lst): return sum(lst)/len(lst) if lst else 0\nassert avg([]) == 0 # 空列表测试通过",
    ]
    for i, output in enumerate(outputs):
        ok, reason = engine._verify_outcome(bug_fix_task, output)
        check(f"bug-fix 输出{i+1} 通过验证", ok, reason)

    bad_output = "天气真不错，适合出去散步"
    ok_bad, _ = engine._verify_outcome(bug_fix_task, bad_output)
    check("bug-fix 无关输出被拒绝", not ok_bad)


def test_n2_fibonacci_verify():
    print("\n" + "=" * 60)
    print("N2. fibonacci / refactor 验证改进")

    from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkTask

    engine = BenchmarkEngine(data_dir=str(OUT_DIR / "bench_n2"))

    fib_task = BenchmarkTask(
        id="code-fibonacci",
        description="fib",
        category="coding",
        expected_outcome="函数正确实现，输出结果为 '55'",
    )
    outputs = [
        "fib(10) = 55\nFunction implemented successfully.",
        "输出: 55",
        "Fibonacci(10) 的结果是 55。",
        "def fib(n):\n  a,b=0,1\n  for _ in range(n): a,b=b,a+b\n  return a\nprint(fib(10))  # 55",
    ]
    for i, output in enumerate(outputs):
        ok, reason = engine._verify_outcome(fib_task, output)
        check(f"fibonacci 输出{i+1} 通过", ok, reason)

    no55 = "函数实现完成，运行结果正确。"
    ok_no55, reason_no55 = engine._verify_outcome(fib_task, no55)
    check("fibonacci 无55被拒绝", not ok_no55, reason_no55)

    refactor_task = BenchmarkTask(
        id="code-refactor",
        description="refactor",
        category="coding",
        expected_outcome="使用'列表推导式'重构代码",
    )
    ok_ref, _ = engine._verify_outcome(
        refactor_task, "result = [i*i for i in range(10) if i % 2 == 0] # 列表推导式"
    )
    check("refactor 输出通过", ok_ref)


def test_default_tasks_updated():
    print("\n" + "=" * 60)
    print("默认任务定义检查")

    from openakita.evolution.benchmark import _DEFAULT_BENCHMARK_TASKS

    tasks_by_id = {t["id"]: t for t in _DEFAULT_BENCHMARK_TASKS}

    bug_fix = tasks_by_id["code-bug-fix"]
    check(
        "bug-fix 使用引号关键词",
        "'空列表'" in bug_fix["expected_outcome"] and "'测试'" in bug_fix["expected_outcome"],
        bug_fix["expected_outcome"],
    )

    fib = tasks_by_id["code-fibonacci"]
    check(
        "fibonacci 使用引号 '55'",
        "'55'" in fib["expected_outcome"],
        fib["expected_outcome"],
    )

    refactor = tasks_by_id["code-refactor"]
    check(
        "refactor 使用引号关键词",
        "'列表推导式'" in refactor["expected_outcome"],
        refactor["expected_outcome"],
    )


def test_production_tasks_updated():
    print("\n" + "=" * 60)
    print("生产 tasks.json 检查")

    prod_path = Path(r"D:\Akita\workspaces\default\data\evolution\benchmarks\tasks.json")
    if not prod_path.exists():
        print("  [SKIP] 生产 tasks.json 不存在")
        return

    tasks = json.loads(prod_path.read_text(encoding="utf-8"))
    tasks_by_id = {t["id"]: t for t in tasks}

    check(
        "生产 bug-fix 使用引号",
        "'空列表'" in tasks_by_id["code-bug-fix"]["expected_outcome"],
    )
    check(
        "生产 fibonacci 使用引号 '55'",
        "'55'" in tasks_by_id["code-fibonacci"]["expected_outcome"],
    )
    check(
        "生产 refactor 使用引号",
        "'列表推导式'" in tasks_by_id["code-refactor"]["expected_outcome"],
    )


def main():
    clean()
    print("=" * 60)
    print("  自进化数据审查修复验证")
    print("=" * 60)

    test_p1_baseline_update_in_code()
    test_p1_baseline_update_functional()
    test_p2_verify_outcome_stability()
    test_n2_fibonacci_verify()
    test_default_tasks_updated()
    test_production_tasks_updated()

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
