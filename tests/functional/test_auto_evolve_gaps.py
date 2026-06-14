"""
自进化 6 种 gap 全覆盖 + 速率限制 功能测试 (LMStudio Live)

测试内容:
  1. EVOLVABLE_GAPS 包含全部 7 种 gap
  2. respond_to_failure() 每种 gap 返回正确 action
  3. 速率限制 (_check_gap_rate) 生效
  4. 进化历史 (_log_evolution) 写入 evolution_history.jsonl
  5. reasoning_engine 门控已扩展
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-needed")
os.environ.setdefault("DEFAULT_MODEL", "qwen/qwen3.5-9b")

PASS = 0
FAIL = 0
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


# ====================================================================
# 1. EVOLVABLE_GAPS 完整性
# ====================================================================
def test_evolvable_gaps():
    print("\n" + "=" * 60)
    print("1. EVOLVABLE_GAPS 完整性")

    from openakita.evolution.auto_evolve import EVOLVABLE_GAPS

    expected = {
        "missing_tool", "insufficient_docs",
        "missing_guardrail", "weak_verification",
        "poor_context_engineering", "supervision_gap",
        "budget_misconfigured",
    }
    actual = set(EVOLVABLE_GAPS)
    check("包含全部 7 种", actual == expected, f"差: {expected - actual}")

    for gap in expected:
        check(f"  {gap}", gap in actual)


# ====================================================================
# 2. respond_to_failure() 每种 gap
# ====================================================================
async def test_respond_to_failure():
    print("\n" + "=" * 60)
    print("2. respond_to_failure() 每种 gap 响应")

    from openakita.core.agent import Agent
    from openakita.core.brain import Brain
    from openakita.evolution.auto_evolve import AutoEvolver

    brain = Brain(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
        model=os.environ["DEFAULT_MODEL"],
    )
    agent = Agent(brain=brain, name="test-evolve")
    evolver = AutoEvolver(agent)

    task = "测试任务描述"

    # 2a: 无效 gap → skip
    result = await evolver.respond_to_failure(task, "nonexistent_gap")
    check("无效 gap → skip", result.action == "skip")

    # 2b: missing_tool → evolved或skip (取决于 LLM 分析)
    result = await evolver.respond_to_failure(task, "missing_tool")
    check("missing_tool → 不崩溃", result.action in ("skip", "evolved", "failed", "partial"))

    # 2c: insufficient_docs
    result = await evolver.respond_to_failure(task, "insufficient_docs")
    check("insufficient_docs → 不崩溃", result.action in ("skip", "evolved", "failed", "partial"))

    # 2d: supervision_gap → evolved (config adjustment)
    result = await evolver.respond_to_failure(task, "supervision_gap")
    check("supervision_gap → flagged", result.action == "flagged")
    check("  原因非空", bool(result.reason))

    # 2e: poor_context_engineering
    result = await evolver.respond_to_failure(task, "poor_context_engineering")
    check("poor_context_engineering → flagged", result.action == "flagged")
    check("  原因非空", bool(result.reason))

    # 2f: budget_misconfigured
    result = await evolver.respond_to_failure(task, "budget_misconfigured")
    check("budget_misconfigured → flagged", result.action == "flagged")

    # 2g: weak_verification
    result = await evolver.respond_to_failure(task, "weak_verification")
    check("weak_verification → 不崩溃", result.action in ("skip", "evolved", "failed"))

    # 2h: missing_guardrail
    result = await evolver.respond_to_failure(task, "missing_guardrail")
    check("missing_guardrail → flagged", result.action == "flagged")
    check("  原因非空", bool(result.reason))

    await agent.close() if hasattr(agent, "close") else None


# ====================================================================
# 3. 速率限制
# ====================================================================
async def test_rate_limiting():
    print("\n" + "=" * 60)
    print("3. 速率限制")

    from openakita.core.agent import Agent
    from openakita.core.brain import Brain
    from openakita.evolution.auto_evolve import AutoEvolver, _GAP_RATE_LIMIT_S

    brain = Brain(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
        model=os.environ["DEFAULT_MODEL"],
    )
    agent = Agent(brain=brain, name="test-rate")
    evolver = AutoEvolver(agent)

    check("_GAP_RATE_LIMIT_S = 60", _GAP_RATE_LIMIT_S == 60)
    check("_last_gap_trigger 初始为空", len(evolver._last_gap_trigger) == 0)

    # 第一次：允许
    ok1 = evolver._check_gap_rate("supervision_gap")
    check("首次触发 → True", ok1)

    # 第二次 (立即)：拒绝
    ok2 = evolver._check_gap_rate("supervision_gap")
    check("立即重复 → False", not ok2)

    # 不同类型不互斥
    ok3 = evolver._check_gap_rate("budget_misconfigured")
    check("不同类型 → True (不互斥)", ok3)

    # 检查内部状态
    check("_last_gap_trigger 记录 2 种", len(evolver._last_gap_trigger) == 2)
    check("supervision_gap 有时间戳", "supervision_gap" in evolver._last_gap_trigger)
    check("budget 有时间戳", "budget_misconfigured" in evolver._last_gap_trigger)

    await agent.close() if hasattr(agent, "close") else None


# ====================================================================
# 4. 进化历史 (.jsonl)
# ====================================================================
async def test_evolution_history():
    print("\n" + "=" * 60)
    print("4. 进化历史记录")

    from openakita.core.agent import Agent
    from openakita.core.brain import Brain
    from openakita.evolution.auto_evolve import AutoEvolver

    brain = Brain(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
        model=os.environ["DEFAULT_MODEL"],
    )
    agent = Agent(brain=brain, name="test-history")
    evolver = AutoEvolver(agent)

    task = "测试进化历史记录"

    # 触发几种 gap → 应写入 history
    await evolver.respond_to_failure(task, "supervision_gap")
    await evolver.respond_to_failure(task, "budget_misconfigured")
    await evolver.respond_to_failure(task, "missing_guardrail")
    await evolver.respond_to_failure(task, "weak_verification")

    log_path = evolver._data_dir / "evolution_history.jsonl"
    check("evolution_history.jsonl 存在", log_path.exists())

    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        check(f"至少 4 条记录 (实际 {len(lines)})", len(lines) >= 4)

        for i, line in enumerate(lines[:4]):
            try:
                entry = json.loads(line)
                check(
                    f"  记录 {i+1} 有 ts/gap/description/action/detail",
                    all(k in entry for k in ("ts", "gap", "description", "action", "detail")),
                )
            except Exception:
                check(f"  记录 {i+1} 为合法 JSON", False)

    await agent.close() if hasattr(agent, "close") else None


# ====================================================================
# 5. reasoning_engine 门控
# ====================================================================
def test_reasoning_engine_gate():
    print("\n" + "=" * 60)
    print("5. reasoning_engine 门控")

    import inspect
    from openakita.core import reasoning_engine

    src = inspect.getsource(reasoning_engine)
    # 检查门控条件
    check("门控包含 auto_evolve_enabled", "auto_evolve_enabled" in src)
    check("门控检查 hasattr harness_gap", "harness_gap" in src)
    # 新门控: 排除 none 和空字符串
    check("门控排除 none", 'not in ("none", "")' in src or 'not in (\'none\', \'\')' in src)

    # 验证 HarnessGap 枚举值
    from openakita.evolution.failure_analysis import HarnessGap
    all_gaps = {g.value for g in HarnessGap}
    check("HarnessGap 枚举包含全部 7 种", len(all_gaps) >= 7)
    check("  none 存在", "none" in all_gaps)


# ====================================================================
async def amain():
    print("=" * 60)
    print("自进化 6 种 gap 全覆盖 功能测试 (LMStudio)")
    print("=" * 60)

    test_evolvable_gaps()
    await test_respond_to_failure()
    await test_rate_limiting()
    await test_evolution_history()
    test_reasoning_engine_gate()

    total = PASS + FAIL
    print("\n" + "=" * 60)
    print(f"  总计: {total}  通过: {PASS}  失败: {FAIL}")
    print("=" * 60)

    if FAILED:
        print(f"\n  失败项 ({len(FAILED)}):")
        for t in FAILED:
            print(f"    - {t}")

    return 0 if FAIL == 0 else 1


def main():
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
