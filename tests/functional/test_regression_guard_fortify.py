"""回归围栏加固验证"""
from __future__ import annotations

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

OUT_DIR = _project_root / "data" / "test_regression_guard"
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


def test_anchor_update_on_significant_improvement():
    print("\n" + "=" * 60)
    print("1. 锚点跟随显著提升")

    from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkMetrics, BenchmarkReport

    bd = OUT_DIR / "bench1"
    engine = BenchmarkEngine(data_dir=str(bd))

    r1 = BenchmarkReport(
        timestamp="2026-06-18T19:00:00",
        metrics=BenchmarkMetrics(success_rate=0.5, avg_tokens=100000, avg_time=10.0),
    )
    engine.save_as_baseline(r1)

    orig = json.loads((bd / "original_baseline.json").read_text("utf-8"))
    check("首次保存 original=0.5", abs(orig["metrics"]["success_rate"] - 0.5) < 0.01)

    r2 = BenchmarkReport(
        timestamp="2026-06-18T19:05:00",
        metrics=BenchmarkMetrics(success_rate=0.6, avg_tokens=90000, avg_time=9.0),
    )
    engine.save_as_baseline(r2)
    orig2 = json.loads((bd / "original_baseline.json").read_text("utf-8"))
    check("小幅提升 0.5→0.6 (delta=0.1<0.15): 锚点不更新", abs(orig2["metrics"]["success_rate"] - 0.5) < 0.01)

    r3 = BenchmarkReport(
        timestamp="2026-06-18T19:10:00",
        metrics=BenchmarkMetrics(success_rate=1.0, avg_tokens=80000, avg_time=8.0),
    )
    engine.save_as_baseline(r3)
    orig3 = json.loads((bd / "original_baseline.json").read_text("utf-8"))
    check("大幅提升 0.5→1.0 (delta=0.5>0.15): 锚点更新到 1.0", abs(orig3["metrics"]["success_rate"] - 1.0) < 0.01)

    bl = json.loads((bd / "baseline.json").read_text("utf-8"))
    check("baseline 始终为最新 (1.0)", abs(bl["metrics"]["success_rate"] - 1.0) < 0.01)


def test_relative_regression_floor():
    print("\n" + "=" * 60)
    print("2. 相对容差围栏 (anchor × 0.80)")

    from openakita.evolution.experiment_loop import _REGRESSION_GUARD_RATIO

    check("_REGRESSION_GUARD_RATIO = 0.80", abs(_REGRESSION_GUARD_RATIO - 0.80) < 0.001)

    cases = [
        (1.0, 0.85, True, "anchor=1.0, current=0.85 > floor=0.80 → 通过"),
        (1.0, 0.79, False, "anchor=1.0, current=0.79 < floor=0.80 → 回滚"),
        (0.8, 0.65, True, "anchor=0.8, current=0.65 > floor=0.64 → 通过"),
        (0.8, 0.63, False, "anchor=0.8, current=0.63 < floor=0.64 → 回滚"),
        (0.5, 0.41, True, "anchor=0.5, current=0.41 > floor=0.40 → 通过"),
        (0.5, 0.39, False, "anchor=0.5, current=0.39 < floor=0.40 → 回滚"),
    ]
    for anchor_sr, current_sr, should_pass, desc in cases:
        floor = anchor_sr * _REGRESSION_GUARD_RATIO
        passes = current_sr >= floor
        check(desc, passes == should_pass, f"floor={floor:.2f}")


def test_per_experiment_anchor_unchanged():
    print("\n" + "=" * 60)
    print("3. per-experiment 锚定检查不变 (绝对容差)")

    from openakita.evolution.experiment_loop import ExperimentLoop, _MAX_REGRESSION_TOLERANCE

    check("_MAX_REGRESSION_TOLERANCE = 0.10 (不变)", abs(_MAX_REGRESSION_TOLERANCE - 0.10) < 0.001)

    old = {"success_rate": 0.85, "avg_tokens": 100, "avg_time": 1.0}
    anchor = {"success_rate": 1.0}
    new_ok = {"success_rate": 0.91, "avg_tokens": 90, "avg_time": 0.9}
    new_bad = {"success_rate": 0.86, "avg_tokens": 90, "avg_time": 0.9}

    check(
        "sr=0.91 > anchor-0.10=0.90 → 通过锚定检查",
        ExperimentLoop._is_improvement(old, new_ok, threshold=0.0, anchor_metrics=anchor),
    )
    check(
        "sr=0.86 < anchor-0.10=0.90 → 被锚定拒绝",
        not ExperimentLoop._is_improvement(old, new_bad, threshold=0.0, anchor_metrics=anchor),
    )


def test_production_data_correct():
    print("\n" + "=" * 60)
    print("4. 生产数据正确")

    base = Path(r"D:\Akita\workspaces\default\data\evolution\benchmarks")
    if not base.exists():
        print("  [SKIP] 生产数据不存在")
        return

    bl = json.loads((base / "baseline.json").read_text("utf-8"))
    ob = json.loads((base / "original_baseline.json").read_text("utf-8"))

    check("baseline = 1.0", abs(bl["metrics"]["success_rate"] - 1.0) < 0.01)
    check("original_baseline = 1.0 (已加固)", abs(ob["metrics"]["success_rate"] - 1.0) < 0.01)

    floor = ob["metrics"]["success_rate"] * 0.80
    check(f"围栏地板 = {floor:.1f} (anchor × 0.80)", abs(floor - 0.80) < 0.01)


def test_source_code_uses_ratio():
    print("\n" + "=" * 60)
    print("5. 代码使用相对容差")

    import inspect
    from openakita.evolution.experiment_loop import ExperimentLoop

    src = inspect.getsource(ExperimentLoop._run_cycle_locked)
    check("使用 _REGRESSION_GUARD_RATIO", "_REGRESSION_GUARD_RATIO" in src)
    check("不再使用 anchor_sr - tolerance", "anchor_sr - tolerance" not in src)


def main():
    clean()
    print("=" * 60)
    print("  回归围栏加固验证")
    print("=" * 60)

    test_anchor_update_on_significant_improvement()
    test_relative_regression_floor()
    test_per_experiment_anchor_unchanged()
    test_production_data_correct()
    test_source_code_uses_ratio()

    print("\n" + "=" * 60)
    print(f"  结果: {PASS} passed, {FAIL} failed")
    if FAILED:
        print("\n  失败项:")
        for f in FAILED:
            print(f"    x {f}")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
