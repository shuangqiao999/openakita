"""
LMStudio 全流程功能测试

测试自进化系统在本地 LLM 下的完整工作流程:
1. JSON fence 剥离 + parsing
2. LLM 假设生成
3. LLM 模式总结
4. LLM 分析性能数据
5. LLM 审计提案
6. Agent 执行 benchmark 任务

运行: python tests/functional/test_evolution_live.py
"""

import json
import re
import urllib.error
import urllib.request

LMSTUDIO_BASE = "http://localhost:1234/v1"
TIMEOUT = 600


def _get_model():
    try:
        req = urllib.request.Request(f"{LMSTUDIO_BASE}/models")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
            models = [m["id"] for m in data.get("data", [])]
            return models[0] if models else "auto"
    except Exception as e:
        print(f"[WARNING] 无法检测模型: {e}, 使用 auto")
        return "auto"


MODEL = _get_model()
print(f"模型: {MODEL}")


def chat(prompt, max_tokens=256):
    """调用 LMStudio chat completions"""
    data = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2, "max_tokens": max_tokens, "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(f"{LMSTUDIO_BASE}/chat/completions", data=data)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            result = json.loads(r.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        return f"[HTTP {e.code}] {body[:200]}"
    except Exception as e:
        return f"[ERROR] {type(e).__name__}: {e}"


def strip_fences(text):
    """去除 LLM 返回中的 Markdown fence 和额外文本"""
    from openakita.evolution import strip_json_fences
    return strip_json_fences(text)


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


# ================================================================
# 测试 1: JSON fence 剥离
# ================================================================
print("\n=== 测试 1: JSON fence 剥离 ===")
resp = chat('输出JSON: {"status": "ok", "value": 42}', max_tokens=100)
print(f"  原始: {resp[:80]}")
cleaned = strip_fences(resp)
print(f"  剥离后: {cleaned[:80]}")
try:
    d = json.loads(cleaned)
    test("JSON 解析成功", "status" in d)
except Exception as e:
    test("JSON 解析成功", False, str(e)[:60])

# 测试 1b: 带说明文本的 JSON
resp = chat('下面是结果:\n{"skip": true}', max_tokens=60)
cleaned = strip_fences(resp)
try:
    d = json.loads(cleaned)
    test("LLM 附加说明文本后 JSON 可提取", d.get("skip") is True)
except Exception as e:
    test("LLM 附加说明文本后 JSON 可提取", False, str(e)[:60])


# ================================================================
# 测试 2: LLM 模式总结 (PatternLearner 功能)
# ================================================================
print("\n=== 测试 2: LLM 模式总结 ===")
resp = chat(
    "工具调用序列: read_file → grep → edit_file → read_file → read_lints\n"
    "总结为一句话 best practice（不要加引号，只输出一行）: 在修改代码文件时，应该",
    max_tokens=80
)
print(f"  结果: {resp[:120]}")
test("模式总结非空", len(resp.strip()) > 5)
test("模式总结长度合理", len(resp) < 300)


# ================================================================
# 测试 3: LLM 分析性能数据 (ResearchOrg Analyst 功能)
# ================================================================
print("\n=== 测试 3: LLM 分析性能数据 ===")
resp = chat(
    '性能指标: 成功率 80%, 平均 token 5000\n'
    '最近失败: web_search 超时, file_not_found 错误\n'
    '输出 JSON 数组，只输出 JSON 不要解释:\n'
    '[{"opportunity": "描述", "priority": 1-10, "category": "prompt/tool/memory/strategy"}]\n'
    '示例: [{"opportunity": "web_search 超时，需要增加重试机制", "priority": 8, "category": "tool"}]',
    max_tokens=300
)
print(f"  原始: {resp[:150]}")
cleaned = strip_fences(resp)
try:
    d = json.loads(cleaned)
    test("Analyst 返回有效 JSON 数组", isinstance(d, list) and len(d) > 0,
         f"数组长度={len(d) if isinstance(d, list) else 'N/A'}")
    if isinstance(d, list) and d:
        test("包含 opportunity 字段", "opportunity" in d[0])
        test("包含 priority 字段", "priority" in d[0])
        test("包含 category 字段", "category" in d[0])
except Exception as e:
    test("Analyst JSON 解析", False, str(e)[:60])


# ================================================================
# 测试 4: LLM 审计提案 (Safety Auditor 功能)
# ================================================================
print("\n=== 测试 4: LLM 审计提案 ===")
resp = chat(
    '修改方案: 修改 identity/AGENT.md 中的工具使用指南，\n'
    '将 "web_search" 替换为 "优先使用 fetch_bookmarked 从权威源获取"\n'
    '请检查安全性。输出 JSON:\n'
    '{"approved": true, "reason": "安全评估理由", "risk_level": "low"}\n'
    '只输出 JSON，不要解释。',
    max_tokens=100
)
print(f"  原始: {resp[:120]}")
cleaned = strip_fences(resp)
try:
    d = json.loads(cleaned)
    test("Auditor 返回有效 JSON", isinstance(d, dict), f"类型={type(d).__name__}")
    test("包含 approved", "approved" in d)
    test("包含 risk_level", "risk_level" in d)
    print(f"  approved={d.get('approved')}, risk={d.get('risk_level')}, reason={d.get('reason', '')[:40]}")
except Exception as e:
    test("Auditor JSON 解析", False, str(e)[:60])


# ================================================================
# 测试 5: LLM 生成实验假设 (ExperimentLoop 功能)
# ================================================================
print("\n=== 测试 5: LLM 生成实验假设 ===")
resp = chat(
    'Agent 系统性能: 成功率 80%，平均 token 5000\n'
    '可修改文件: identity/AGENT.md（Agent 行为指令）\n'
    '当前 prompt 内容: "## 工具使用原则\n- 编辑文件前必须先 read_file 确认当前内容"\n'
    '请提出一个具体的改进方案。输出 JSON:\n'
    '{"target": "identity/AGENT.md", "description": "改进描述", '
    '"rationale": "为什么有效", "proposed_change": "新内容", '
    '"original_fragment": "原文，精确匹配"}\n'
    '如果无需修改，返回 {"skip": true}',
    max_tokens=500
)
print(f"  输出长度: {len(resp)} 字符")
cleaned = strip_fences(resp)
try:
    d = json.loads(cleaned)
    if d.get("skip"):
        test("LLM 判断无需修改", True, "skip=true (合理)")
    else:
        test("包含 target", "target" in d)
        test("包含 description", "description" in d)
        test("包含 rationale", "rationale" in d)
        test("包含 proposed_change", "proposed_change" in d)
        test("包含 original_fragment", "original_fragment" in d)
        print(f"  target={d.get('target')}, desc={d.get('description', '')[:50]}")
except Exception as e:
    test("假设 JSON 解析", False, str(e)[:60])


# ================================================================
# 测试 6: Benchmark 引擎创建 + 验证
# ================================================================
print("\n=== 测试 6: Benchmark 引擎 ===")
from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkTask

engine = BenchmarkEngine(data_dir="/tmp/evolve_test_bench")
tasks = engine.load_tasks()
test("加载默认任务", len(tasks) == 8, f"实际={len(tasks)}")

# 验证 verification 逻辑
task = BenchmarkTask(id="test", description="", category="test",
                     expected_outcome="输出 '4950'")
ok, _ = engine._verify_outcome(task, "计算结果 4950")
test("验证通过(匹配数字)", ok)

fail, reason = engine._verify_outcome(task, "结果不对")
test("验证失败(数字不匹配)", not fail, reason[:40])

# 验证空输出
fail, reason = engine._verify_outcome(task, "")
test("验证失败(空输出)", not fail, reason[:40])


# ================================================================
# 测试 7: Fuzzy match 功能
# ================================================================
print("\n=== 测试 7: Fuzzy match ===")
from openakita.evolution.experiment_loop import ExperimentLoop

result, err = ExperimentLoop._fuzzy_match_and_replace(
    "hello world\ngoodbye\n", "hello world\ngoodbye\n", "REPLACED\n"
)
test("精确匹配", result == "REPLACED\n")

result, err = ExperimentLoop._fuzzy_match_and_replace(
    "line one\n  line  two\nline three\n",
    "line one\nline two\nline three\n",
    "REPLACED\n"
)
test("空白差异模糊匹配", result is not None and "REPLACED" in result)

result, err = ExperimentLoop._fuzzy_match_and_replace(
    "hello world", "xyzpdq abc", "x"
)
test("完全无法匹配", result is None, err[:40])


# ================================================================
# 测试 8: AutoEvolver 功能
# ================================================================
print("\n=== 测试 8: AutoEvolver ===")
from openakita.evolution.auto_evolve import AutoEvolver

evolver = AutoEvolver(None)
result = evolver._is_recently_processed("test_cap_xyz")
test("未处理的能力返回 False", not result)
evolver._mark_processed("test_cap_xyz")
result2 = evolver._is_recently_processed("test_cap_xyz")
test("标记后返回 True", result2)

# 实例隔离
e2 = AutoEvolver(None)
test("不同实例去重隔离", not e2._is_recently_processed("test_cap_xyz"))


# ================================================================
# 总结
# ================================================================
print("\n" + "=" * 50)
print(f"结果: {pass_count} PASS, {fail_count} FAIL (共 {pass_count + fail_count} 项)")
print("=" * 50)
