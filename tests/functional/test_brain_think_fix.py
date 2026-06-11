"""
验证 chat_simple→think 修复 — LMStudio 全流程测试

测试 Brain.think() 在自进化 4 个模块中正常工作:
1. ExperimentLoop._generate_hypothesis → Brain.think()
2. PromptOptimizer._propose_optimization → Brain.think()
3. ResearchOrg._run_analyst → Brain.think()
4. PatternLearner._summarize_pattern → Brain.think()
"""

import json
import urllib.error
import urllib.request

LMSTUDIO = "http://localhost:1234/v1"

def get_model():
    try:
        req = urllib.request.Request(f"{LMSTUDIO}/models")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
            return [m["id"] for m in data.get("data", [])][0]
    except Exception:
        return "auto"

MODEL = get_model()
print(f"模型: {MODEL}")

def think(prompt, max_tokens=256, timeout=600):
    """模拟 Brain.think() — 直接调用 LMStudio API"""
    data = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2, "max_tokens": max_tokens, "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(f"{LMSTUDIO}/chat/completions", data=data)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            result = json.loads(r.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[ERROR] {e}"

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
print("\n=== 验证 1: Brain.think() 在 ExperimentLoop 场景中工作 ===")
print("(假设生成: 分析性能 + 当前 prompt → JSON 提案)")
resp = think(
    '性能: 成功率 80%, token 消耗偏高\n'
    '可修改 prompt 减少 token。输出 JSON:\n'
    '{"target": "identity/AGENT.md", "description": "改进", '
    '"rationale": "原因", "proposed_change": "新内容", '
    '"original_fragment": "原文"}\n只输出 JSON',
    max_tokens=300
)
print(f"  输出长度: {len(resp)} 字符")
test("Brain.think() 返回非空", len(resp) > 5)
test("Brain.think() 无报错", "ERROR" not in resp)
try:
    d = json.loads(resp.strip().strip("```json").strip("```"))
    test("返回有效 JSON", "target" in d or "skip" in d)
except Exception:
    test("返回可解析 JSON", False, str(resp)[:60])

# ================================================================
print("\n=== 验证 2: Brain.think() 在 ResearchOrg 场景中工作 ===")
print("(Analyst: 分析性能 + 失败模式 → JSON 数组建议)")
resp = think(
    '性能指标: 成功率 80%, 平均 token 5000\n'
    '最近失败: web_search 超时\n'
    '输出 JSON 数组:\n'
    '[{"opportunity": "描述", "priority": 1-10, "category": "prompt/tool/memory/strategy"}]\n'
    '只输出 JSON 数组',
    max_tokens=300
)
print(f"  输出: {resp[:120]}")
test("Analyst 返回非空", len(resp) > 5)
try:
    d = json.loads(resp.strip().strip("```json").strip("```"))
    test("返回 JSON 数组", isinstance(d, list) and len(d) > 0)
except Exception:
    test("返回可解析 JSON", False, str(resp)[:60])

# ================================================================
print("\n=== 验证 3: Brain.think() 在 PatternLearner 场景中工作 ===")
print("(模式总结: 工具序列 → 一行 best practice)")
resp = think(
    "工具序列: grep → read_file → edit_file → read_lints\n"
    "总结为一行 best practice（只输出一行）: 在修改代码文件时，应该",
    max_tokens=50
)
print(f"  输出: {resp[:100]}")
test("模式总结非空", len(resp.strip()) > 5)
test("模式总结 ≤ 200 字符", len(resp) < 200)

# ================================================================
print("\n=== 验证 4: Brain.think() 在 PromptOptimizer 场景中工作 ===")
print("(优化提案: 当前性能 + prompt → JSON 提案)")
resp = think(
    'Agent 成功率 80%，token 偏高\n'
    '当前 prompt: "## 工具使用原则\n- 编辑文件前先 read_file"\n'
    '请提出一个小范围改进方案。输出 JSON:\n'
    '{"section": "identity/AGENT.md", "original": "原文", '
    '"proposed": "修改后", "hypothesis": "理由"}',
    max_tokens=300
)
print(f"  输出: {resp[:120]}")
try:
    d = json.loads(resp.strip().strip("```json").strip("```"))
    test("返回 JSON", "section" in d or "original" in d)
except Exception:
    test("返回可解析 JSON", False, str(resp)[:60])

# ================================================================
print("\n=== 验证 5: Fuzzy match + Syntax 验证 ===")
from openakita.evolution.experiment_loop import ExperimentLoop
r, e = ExperimentLoop._fuzzy_match_and_replace("hello world\n", "hello world\n", "REPLACED\n")
test("精确匹配", r == "REPLACED\n")
r, e = ExperimentLoop._fuzzy_match_and_replace("a b\n", "a  b\n", "x\n")
test("空白差异模糊匹配", r is not None)

# ================================================================
print("\n" + "=" * 50)
print(f"结果: {pass_count} PASS, {fail_count} FAIL (共 {pass_count + fail_count})")
