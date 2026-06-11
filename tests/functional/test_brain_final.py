"""
全方位无死角测试 - 真实 Brain.think() → Response.content → 自进化全链路

验证:
1. Brain.think() 返回 Response 对象，.content 为纯字符串
2. strip_json_fences 能从 LLM 输出正确提取 JSON
3. ExperimentLoop 假设生成 → JSON 解析
4. ResearchOrg Analyst → JSON 数组解析
5. PatternLearner → 一句话总结
6. PromptOptimizer → JSON 提案
7. Safety Auditor → JSON 审计
8. JSON 恢复边界: 前置文本/后置文本/纯JSON/fence包裹
9. 1并行 LLM 调用 (无并发压力)

运行: python tests/functional/test_brain_final.py
"""

import asyncio
import json
from openakita.core.brain import Brain
from openakita.config import settings
from openakita.evolution import strip_json_fences

pass_count = 0
fail_count = 0


def test(name, condition, detail=""):
    global pass_count, fail_count
    if condition:
        pass_count += 1
        print(f"  [PASS] {name}")
    else:
        fail_count += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


async def main():
    global pass_count, fail_count

    # ── 初始化 Brain ──
    ef = settings.data_dir / "llm_endpoints.json"
    if not ef.exists():
        ef = settings.working_dir / "data" / "llm_endpoints.json"

    model_id = None
    if ef.exists():
        data = json.loads(ef.read_text(encoding="utf-8"))
        model_id = list(data.keys())[0]

    if not model_id:
        print("错误: 未找到 LLM 配置")
        return

    brain = Brain(api_key="not-needed")
    print(f"模型: {model_id}")
    print(f"Response 类: {Brain.__module__}.Response\n")

    # ================================================================
    print("=== 1. Brain.think() 基础验证 ===")
    resp = await brain.think("说 hello")
    test("返回类型是 Response", type(resp).__name__ == "Response",
         f"实际: {type(resp).__name__}")
    test("有 .content 属性", hasattr(resp, "content"))
    test(".content 是字符串", isinstance(resp.content, str))
    test(".content 非空", len(resp.content) > 0,
         f"长度: {len(resp.content)}")

    # ================================================================
    print("\n=== 2. strip_json_fences + LLM 实际输出 ===")

    # 2a: 纯 JSON（无 fence）
    resp = await brain.think('输出JSON: {"status": "ok", "value": 42}\n只输出JSON不要解释')
    raw = resp.content
    print(f"  LLM 输出: {raw[:80]}")
    cleaned = strip_json_fences(raw)
    try:
        d = json.loads(cleaned)
        test("2a 纯JSON解析", d.get("status") == "ok")
    except Exception as e:
        test("2a 纯JSON解析", False, str(e)[:50])

    # 2b: fence 包裹
    # (有些模型会自动加 ```json fence)
    resp = await brain.think('输出JSON: {"approved": true, "reason": "安全"}\n只输出JSON不要解释')
    cleaned = strip_json_fences(resp.content)
    try:
        d = json.loads(cleaned)
        test("2b fence处理", "approved" in d)
    except Exception as e:
        test("2b fence处理", False, str(e)[:50])

    # 2c: JSON 前有说明文字
    resp = await brain.think('下面是结果:\n{"skip": true}\n请查看')
    cleaned = strip_json_fences(resp.content)
    try:
        d = json.loads(cleaned)
        test("2c 前置文本提取", d.get("skip") is True)
    except Exception as e:
        test("2c 前置文本提取", False, f"{resp.content[:60]} | {e}")

    # ================================================================
    print("\n=== 3. ExperimentLoop 假设生成 ===")
    prompt3 = (
        "性能: 成功率 80%, token 5000, 耗时 10s\n"
        '可修改文件: identity/AGENT.md\n'
        '当前 prompt: "## 工具使用原则\\n- 编辑文件前先 read_file"\n'
        "请提出一个具体改进方案。输出 JSON:\n"
        '{"target": "identity/AGENT.md", "description": "...", '
        '"rationale": "...", "proposed_change": "...", "original_fragment": "..."}\n'
        "只输出JSON，不要解释。"
    )
    resp = await brain.think(prompt3)
    cleaned = strip_json_fences(resp.content)
    try:
        d = json.loads(cleaned)
        if d.get("skip"):
            test("3 假设生成(skip)", True, "LLM判断无需修改")
        else:
            has_keys = all(k in d for k in ["target", "description", "rationale"])
            test("3 假设生成(含所有字段)", has_keys,
                 f"keys={list(d.keys())}" if not has_keys else "")
    except Exception as e:
        test("3 假设生成", False, f"{resp.content[:80]} | {e}")

    # ================================================================
    print("\n=== 4. ResearchOrg Analyst ===")
    prompt4 = (
        "性能: 成功率 80%, token 5000\n"
        "失败: web_search 超时 3 次\n"
        "输出 JSON 数组（只输出JSON）:\n"
        '[{"opportunity": "描述", "priority": 1-10, "category": "prompt/tool/memory/strategy"}]\n'
        "示例: [{\"opportunity\": \"web_search超时\", \"priority\": 8, \"category\": \"tool\"}]"
    )
    resp = await brain.think(prompt4)
    cleaned = strip_json_fences(resp.content)
    try:
        d = json.loads(cleaned)
        test("4 Analyst JSON数组", isinstance(d, list) and len(d) > 0,
             f"长度={len(d) if isinstance(d, list) else 'N/A'}")
        if isinstance(d, list) and d:
            item = d[0]
            test("4 含 opportunity", "opportunity" in item)
            test("4 含 priority", "priority" in item)
            test("4 含 category", "category" in item)
    except Exception as e:
        test("4 Analyst", False, f"{resp.content[:80]} | {e}")

    # ================================================================
    print("\n=== 5. PatternLearner 模式总结 ===")
    prompt5 = (
        "工具序列: grep -> read_file -> edit_file -> read_lints\n"
        "总结为一行 best practice（只输出一行文本，不要引号）:\n"
        "在修改代码文件时，应该"
    )
    resp = await brain.think(prompt5)
    text = resp.content.strip()
    test("5 模式总结非空", len(text) > 5, f"长度={len(text)}")
    test("5 模式总结≤200字符", len(text) <= 200, f"长度={len(text)}")

    # ================================================================
    print("\n=== 6. Safety Auditor 审计 ===")
    prompt6 = (
        "修改方案: 把 identity/AGENT.md 中 web_search 替换为 fetch_bookmarked\n"
        "请检查安全性。输出 JSON:\n"
        '{"approved": true, "reason": "...", "risk_level": "low/medium/high"}\n'
        "只输出JSON"
    )
    resp = await brain.think(prompt6)
    cleaned = strip_json_fences(resp.content)
    try:
        d = json.loads(cleaned)
        test("6 Auditor 含 approved", "approved" in d)
        test("6 Auditor 含 risk_level", "risk_level" in d)
    except Exception as e:
        test("6 Auditor", False, f"{resp.content[:80]} | {e}")

    # ================================================================
    print("\n=== 7. 响应格式多样性测试 ===")

    # 7a: 很长的输出
    resp = await brain.think("列出5个Python内置模块，每行一个", max_tokens=200)
    test("7a 长输出可达", len(resp.content) > 20)

    # 7b: 数字输出
    resp = await brain.think("说一个1-100的数字，只回答数字", max_tokens=10)
    test("7b 数字输出", len(resp.content.strip()) < 50)

    # ================================================================
    print("\n=== 8. _parse_llm_json 端到端 ===")
    # 模拟 evolution 模块的真实调用方式
    from openakita.evolution.experiment_loop import _parse_llm_json

    resp = await brain.think('输出: {"status": "ok"}')
    try:
        d = _parse_llm_json(resp.content)
        test("8 _parse_llm_json 正常", d.get("status") == "ok")
    except Exception as e:
        test("8 _parse_llm_json 正常", False, str(e)[:50])

    # fence 包裹
    resp = await brain.think('输出JSON代码块:\n```json\n{"x": 1}\n```')
    try:
        d = _parse_llm_json(resp.content)
        test("8 fence包裹解析", d.get("x") == 1)
    except Exception as e:
        test("8 fence包裹解析", False, f"{resp.content[:60]} | {e}")

    # ================================================================
    print("\n" + "=" * 60)
    print(f"结果: {pass_count} PASS, {fail_count} FAIL (共 {pass_count + fail_count} 项)")
    print("=" * 60)

    if fail_count == 0:
        print("\n✓ 自进化系统全链路验证通过，可以重新构建部署。")
    else:
        print(f"\n✗ 还有 {fail_count} 项失败，需要修复后重新测试。")


asyncio.run(main())
