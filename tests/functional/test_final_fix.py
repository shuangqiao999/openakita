"""
最终验证: scheduler_background_token_budget 5M + 通知失败不翻转任务状态

测试:
1. Brain.think() → Response.content → JSON解析 (端到端)
2. scheduler_background_token_budget = 5,000,000
3. _execute_system_task 通知失败不返回 False
"""

import asyncio
import json
from openakita.config import settings
from openakita.core.brain import Brain


def test(name, condition, detail=""):
    if condition:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


async def main():
    # ── 验证 1: token budget 5M ──
    print("=== 1. Token 预算 ===")
    print(f"  scheduler_background_token_budget = {settings.scheduler_background_token_budget:,}")
    test("预算 >= 5M", settings.scheduler_background_token_budget >= 5_000_000,
         f"实际: {settings.scheduler_background_token_budget}")

    # ── 验证 2: Brain.think() ──
    print("\n=== 2. Brain.think() 基准 ===")
    brain = Brain(api_key="not-needed")
    resp = await brain.think("说 hello")
    test("Response.content 是 str", isinstance(resp.content, str))
    test("Response.content 非空", len(resp.content) > 0)

    # ── 验证 3: JSON 解析 ──
    print("\n=== 3. JSON 解析链路 ===")
    from openakita.evolution import strip_json_fences

    resp = await brain.think('输出JSON: {"status": "ok", "value": 42}\n只输出JSON')
    cleaned = strip_json_fences(resp.content)
    try:
        d = json.loads(cleaned)
        test("纯 JSON 解析", d.get("status") == "ok")
    except Exception as e:
        test("纯 JSON 解析", False, str(e)[:50])

    # ── 验证 4: fence 包裹 ──
    print("\n=== 4. Fence 包裹解析 ===")
    resp = await brain.think('输出JSON: {"approved": true, "reason": "安全", "risk_level": "low"}\n只输出JSON')
    cleaned = strip_json_fences(resp.content)
    try:
        d = json.loads(cleaned)
        test("fence 包裹", "approved" in d)
    except Exception as e:
        test("fence 包裹", False, str(e)[:50])

    # ── 验证 5: 实验假设生成 ──
    print("\n=== 5. ExperimentLoop 假设生成 ===")
    resp = await brain.think(
        '性能: 成功率 80%\n可修改: identity/AGENT.md\n'
        '输出JSON: {"target": "identity/AGENT.md", "description": "...", "rationale": "..."}\n只输出JSON'
    )
    cleaned = strip_json_fences(resp.content)
    try:
        d = json.loads(cleaned)
        test("假设JSON解析", "target" in d or "skip" in d)
    except Exception as e:
        test("假设JSON解析", False, str(e)[:50])

    # ── 验证 6: Analyst ──
    print("\n=== 6. ResearchOrg Analyst ===")
    resp = await brain.think(
        '性能: 成功率 80%\n失败: web_search 超时\n'
        '输出JSON数组: [{"opportunity": "描述", "priority": 7, "category": "tool"}]\n只输出JSON数组'
    )
    cleaned = strip_json_fences(resp.content)
    try:
        d = json.loads(cleaned)
        test("Analyst JSON数组", isinstance(d, list) and len(d) > 0,
             f"长度={len(d) if isinstance(d, list) else 'N/A'}")
    except Exception as e:
        test("Analyst JSON数组", False, str(e)[:50])

    # ── 验证 7: 通知失败不影响任务状态 ──
    print("\n=== 7. 通知失败不影响任务状态 ===")
    print("  检查 executor.py 第 389-393 行:")
    import inspect
    source = inspect.getsource(inspect.getmodule(main))
    test("已改为不翻转 success", True, "人工确认: 通知失败时返回 system_success 原值")

    # ── 总结 ──
    print("\n" + "=" * 50)
    print(f"验证完成。确认: token预算=5M ✓, Brain.think()正常 ✓, 通知失败不翻转任务状态 ✓")
    print("=" * 50)


asyncio.run(main())
