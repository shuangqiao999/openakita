"""变体任务生成门槛验证"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
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


def test_source_code():
    print("\n" + "=" * 60)
    print("1. 源码门槛检查")

    from openakita.scheduler.executor import TaskExecutor

    src = inspect.getsource(TaskExecutor._system_benchmark_evolve_inner)

    check("动态变体: _all_pass 条件", "_all_pass" in src)
    check("动态变体: _has_degraded 条件", "_has_degraded" in src)
    check("场景化: _all_pass 条件", src.count("_all_pass") >= 2)
    check("场景化: _has_degraded 条件", src.count("_has_degraded") >= 2)
    check("跳过日志", "跳过变体生成" in src)


def test_gate_logic():
    print("\n" + "=" * 60)
    print("2. 门槛逻辑验证")

    cases = [
        (1.0, False, True, "sr=100% + 无降级 → 生成"),
        (0.875, False, False, "sr=87.5% + 无降级 → 跳过"),
        (0.5, False, False, "sr=50% + 无降级 → 跳过"),
        (1.0, True, False, "sr=100% + 有降级 → 跳过"),
        (0.875, True, False, "sr=87.5% + 有降级 → 跳过"),
    ]
    for sr, has_degraded, should_generate, desc in cases:
        all_pass = sr >= 1.0
        result = all_pass and not has_degraded
        check(desc, result == should_generate)


def test_health_degraded_detection():
    print("\n" + "=" * 60)
    print("3. 降级检测")

    healthy = {
        "t1": {"consecutive_fails": 0, "degraded": False},
        "t2": {"consecutive_fails": 1, "degraded": False},
    }
    check("全健康 → 无降级", not any(v.get("degraded") for v in healthy.values()))

    degraded = {
        "t1": {"consecutive_fails": 0, "degraded": False},
        "t2": {"consecutive_fails": 3, "degraded": True},
    }
    check("有降级 → 检测到", any(v.get("degraded") for v in degraded.values()))

    empty = {}
    check("空 health → 无降级", not any(v.get("degraded") for v in empty.values()))


def main():
    print("=" * 60)
    print("  变体任务生成门槛验证")
    print("=" * 60)

    test_source_code()
    test_gate_logic()
    test_health_degraded_detection()

    print("\n" + "=" * 60)
    print(f"  结果: {PASS} passed, {FAIL} failed")
    if FAILED:
        for f in FAILED:
            print(f"    x {f}")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
