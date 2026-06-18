"""baseline 覆盖 bug 修复验证"""
from __future__ import annotations

import inspect
import json
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


def test_code_fix():
    print("\n" + "=" * 60)
    print("1. executor.py 条件逻辑修复")

    src = (_project_root / "src" / "openakita" / "scheduler" / "executor.py").read_text("utf-8")
    check(
        "使用 'and not kept' 而非 'or kept'",
        "not report.baseline_delta and not kept" in src,
    )
    check(
        "旧的 'or kept' 已删除",
        "not report.baseline_delta or kept" not in src,
    )


def test_production_data():
    print("\n" + "=" * 60)
    print("2. 生产数据已修正")

    base = Path(r"D:\Akita\workspaces\default\data\evolution\benchmarks")
    if not base.exists():
        print("  [SKIP] 生产数据目录不存在")
        return

    bl = json.loads((base / "baseline.json").read_text("utf-8"))
    ob = json.loads((base / "original_baseline.json").read_text("utf-8"))

    check("baseline.json 为最新 (100%)", abs(bl["metrics"]["success_rate"] - 1.0) < 0.01)
    check("original_baseline.json 为初始 (50%)", abs(ob["metrics"]["success_rate"] - 0.5) < 0.01)
    check(
        "baseline 时间戳晚于 original",
        bl["timestamp"] > ob["timestamp"],
        f"bl={bl['timestamp']}, ob={ob['timestamp']}",
    )


def test_logic_scenarios():
    print("\n" + "=" * 60)
    print("3. 条件逻辑场景验证")

    cases = [
        ({}, [], True, "首次运行无实验 → 保存初始基线"),
        ({}, [{"action": "keep"}], False, "首次运行有实验 keep → 不覆盖 (实验已保存)"),
        ({"success_rate": 0.1}, [], False, "非首次无实验 → 不保存"),
        ({"success_rate": 0.1}, [{"action": "keep"}], False, "非首次有实验 → 不保存"),
    ]
    for delta, kept, should_save, desc in cases:
        result = not delta and not kept
        check(f"{desc}", result == should_save)


def main():
    print("=" * 60)
    print("  baseline 覆盖 bug 修复验证")
    print("=" * 60)

    test_code_fix()
    test_production_data()
    test_logic_scenarios()

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
