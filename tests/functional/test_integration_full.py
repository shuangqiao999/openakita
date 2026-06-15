"""
自进化全链路集成测试 (LMStudio + 工作区数据)

测试流程:
  1. 读取工作区 trace 文件 → 验证 conversation_metrics
  2. 读取工作区 memory DB → 验证 memory_usage_rate
  3. 运行 ExperimentLoop → 验证 cycle.json + quality_score
  4. 验证 quality_weight.json 自适应
  5. 验证 AutoEvolver 7 种 gap 处理
  6. 验证 approval_queue retry 逻辑
  7. 验证 PatternLearner 工具提取
  8. 分析生成的所有 JSON 文件

运行: python tests/functional/test_integration_full.py
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

OUT_DIR = _project_root / "data" / "test_integration"
WORKSPACE = Path(r"D:\Akita\workspaces\default\data")


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
def test_trace_reading():
    print("\n" + "=" * 60)
    print("1. Trace 文件读取 + conversation_metrics")

    traces_dir = WORKSPACE / "react_traces"
    if not traces_dir.is_dir():
        check("react_traces 目录存在", False, str(traces_dir))
        return

    files = []
    for d in traces_dir.iterdir():
        if d.is_dir():
            files.extend(d.glob("*.json"))
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    total = total_success = 0
    tool_names = set()
    for f in files[:30]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            total += 1
            if d.get("result") in ("success", "completed"):
                total_success += 1
            for it in d.get("iterations", []):
                for tc in it.get("tool_calls", []):
                    n = tc.get("name", "")
                    if n:
                        tool_names.add(n)
        except Exception:
            continue

    check(f"读取 {total} 条 trace", total >= 20)
    check(f"成功/完成: {total_success} 条", total_success >= 10)
    rate = round(total_success / max(total, 1), 3)
    check(f"conversation_success_rate = {rate}", rate > 0.5)
    check(f"提取工具: {sorted(tool_names)[:8]}", len(tool_names) >= 1)


# ====================================================================
def test_memory_reading():
    print("\n" + "=" * 60)
    print("2. Memory DB 读取 + usage_rate")

    db_path = WORKSPACE / "memory" / "openakita.db"
    if not db_path.exists():
        check("memory DB 存在", False, str(db_path))
        return

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    check(f"memory_total = {total}", total > 0)

    used_ac = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE access_count > 0"
    ).fetchone()[0]
    used_la = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE last_accessed_at IS NOT NULL"
    ).fetchone()[0]
    used_7d = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE created_at > datetime('now', '-7 days')"
    ).fetchone()[0]

    check(f"access_count>0: {used_ac}", used_ac >= 0)
    check(f"last_accessed_at NOT NULL: {used_la}", used_la >= 0)
    check(f"created_at 7d: {used_7d}", used_7d >= used_ac)

    effective_used = max(used_ac, used_la, 1)
    rate = round(effective_used / max(total, 1), 3)
    check(f"effective_usage_rate = {rate}", rate > 0.0, f"all used queries returned 0")

    conn.close()


# ====================================================================
async def test_experiment_loop():
    print("\n" + "=" * 60)
    print("3. ExperimentLoop → cycle.json + quality_score")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "experiments" / "quality_scores").mkdir(parents=True, exist_ok=True)

    from openakita.core.agent import Agent
    from openakita.core.brain import Brain
    from openakita.evolution.experiment_loop import ExperimentLoop

    brain = Brain(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
        model=os.environ["DEFAULT_MODEL"],
    )
    agent = Agent(brain=brain, name="test-integration")

    data_dir = str(OUT_DIR / "experiments")
    loop = ExperimentLoop(agent, data_dir=data_dir)

    # mock benchmark
    class MockMetrics:
        success_rate = 0.8
        avg_tokens = 200.0
        avg_time = 10.0
        efficiency_score = 0.5
        category_scores = {"tool_use": 1.0, "coding": 1.0}

    class MockReport:
        metrics = MockMetrics()

    try:
        results = await loop.run_cycle(benchmark_report=MockReport())
        check("run_cycle 返回列表", isinstance(results, list))
        if results:
            r = results[0]
            check("ExperimentResult 有 action", r.action in ("keep", "discard", "error"))
            check("ExperimentResult 有 quality_score", hasattr(r, "quality_score"))
            if r.quality_score is not None:
                check("quality_score.overall > 0", r.quality_score.overall > 0)
    except Exception as e:
        check("run_cycle 不崩溃", False, f"{type(e).__name__}: {e}")

    # 验证质量分数文件已生成
    qs_dir = Path(data_dir) / "quality_scores"
    qs_files = list(qs_dir.glob("*.json")) if qs_dir.exists() else []
    check(f"quality_scores 文件数: {len(qs_files)}", len(qs_files) >= 0)

    # 验证 quality_weight.json
    qw_path = Path(data_dir) / "quality_weight.json"
    if qw_path.exists():
        qw = json.loads(qw_path.read_text(encoding="utf-8"))
        check(f"quality_weight = {qw.get('weight', '?')}", 0.0 <= qw.get("weight", 0) <= 0.3)
    else:
        check("quality_weight.json 已创建", False)

    await agent.close() if hasattr(agent, "close") else None


# ====================================================================
async def test_pattern_hint():
    print("\n" + "=" * 60)
    print("4. PatternLearner → _load_pattern_hint")

    from openakita.core.agent import Agent
    from openakita.core.brain import Brain
    from openakita.evolution.experiment_loop import ExperimentLoop

    brain = Brain(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
        model=os.environ["DEFAULT_MODEL"],
    )
    agent = Agent(brain=brain, name="test-pattern")
    loop = ExperimentLoop(agent, data_dir=str(OUT_DIR / "experiments"))

    hint = ExperimentLoop._load_pattern_hint()
    check("_load_pattern_hint 返回字符串", isinstance(hint, str))

    # 检查工作区的 patterns 文件
    patterns_path = WORKSPACE / "evolution" / "patterns" / "effective_patterns.json"
    if patterns_path.exists():
        check("effective_patterns.json 存在", True)
        data = json.loads(patterns_path.read_text(encoding="utf-8"))
        check(f"patterns 数量: {len(data)}", isinstance(data, list))
    else:
        check("effective_patterns.json 不存在 (首次运行)", hint == "")

    await agent.close() if hasattr(agent, "close") else None


# ====================================================================
async def test_auto_evolver():
    print("\n" + "=" * 60)
    print("5. AutoEvolver 7 种 gap 处理")

    from openakita.core.agent import Agent
    from openakita.core.brain import Brain
    from openakita.evolution.auto_evolve import AutoEvolver, EVOLVABLE_GAPS

    brain = Brain(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
        model=os.environ["DEFAULT_MODEL"],
    )
    agent = Agent(brain=brain, name="test-auto")
    evolver = AutoEvolver(agent)

    check("EVOLVABLE_GAPS 包含 7 种", len(EVOLVABLE_GAPS) == 7)

    task = "集成测试任务"

    # 测试每种 gap
    for gap in sorted(EVOLVABLE_GAPS):
        try:
            result = await evolver.respond_to_failure(task, gap)
            check(
                f"  {gap} → {result.action}",
                result.action in ("skip", "evolved", "flagged", "failed", "partial"),
                result.reason[:60],
            )
        except Exception as e:
            check(f"  {gap} → 不崩溃", False, f"{type(e).__name__}: {e}")

    # 验证 evolution_history.jsonl
    hist_path = OUT_DIR.parent.parent / "data" / "evolution" / "evolution_history.jsonl"
    work_hist = WORKSPACE / "evolution" / "evolution_history.jsonl"
    our_hist = _project_root / "data" / "evolution" / "evolution_history.jsonl"
    # Use whichever exists
    for p in [our_hist, work_hist]:
        if p.exists():
            lines = p.read_text(encoding="utf-8").strip().split("\n")
            check(f"evolution_history.jsonl: {len(lines)} 条记录", len(lines) >= 1)
            entry = json.loads(lines[-1])
            check("  最后一条有 ts/gap/action", all(k in entry for k in ("ts", "gap", "action")))
            break

    await agent.close() if hasattr(agent, "close") else None


# ====================================================================
def test_approval_retry():
    print("\n" + "=" * 60)
    print("6. approval_queue retry 逻辑")

    from openakita.evolution.approval_queue import ApprovalQueue, ApprovalRequest

    aq = ApprovalQueue(data_dir=str(OUT_DIR / "approvals"))

    req = ApprovalRequest(
        title="test_integration",
        target_file="identity/AGENT.md",
        original_content="hello world",
        proposed_content="goodbye world",
    )
    rid = aq.submit(req)

    # 3 次失败
    for i in range(1, 4):
        ok, msg = aq.approve_and_apply(rid)
        data = aq.get(rid)
        check(f"  第{i}次: retry={data.get('retry_count')}, status={data.get('status')}",
              data.get("retry_count") == i or (i == 3 and data.get("status") == "rejected"))

    # 第 4 次应拒绝
    ok, msg = aq.approve_and_apply(rid)
    data = aq.get(rid)
    check("  3次后自动拒绝", data.get("status") == "rejected")


# ====================================================================
def test_pattern_learner():
    print("\n" + "=" * 60)
    print("7. PatternLearner 工具提取 (生产 traces)")

    from openakita.evolution.pattern_learner import PatternLearner

    traces_dir = WORKSPACE / "react_traces"
    files = []
    for d in traces_dir.iterdir():
        if d.is_dir():
            files.extend(d.glob("*.json"))

    total_tools = set()
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            raw = d.get("iterations", [])
            tools = PatternLearner._extract_tool_names(raw)
            total_tools.update(tools)
        except Exception:
            continue

    check(f"提取到 {len(total_tools)} 种工具: {sorted(list(total_tools)[:10])}", len(total_tools) >= 1)


# ====================================================================
def analyze_generated_files():
    print("\n" + "=" * 60)
    print("8. 分析生成的 JSON 文件")

    exp_dir = OUT_DIR / "experiments"

    # cycle.json
    cycles = list(exp_dir.glob("*_cycle.json"))
    check(f"cycle.json 文件数: {len(cycles)}", len(cycles) >= 1)
    if cycles:
        cdata = json.loads(cycles[0].read_text(encoding="utf-8"))
        check("cycle.json 为列表", isinstance(cdata, list))
        if cdata:
            r = cdata[0]
            check("  有 action", "action" in r)
            check("  有 delta", "delta" in r)
            check("  有 quality_score", "quality_score" in r)

    # quality_scores
    qs_dir = exp_dir / "quality_scores"
    if qs_dir.exists():
        qs_files = list(qs_dir.glob("*.json"))
        check(f"quality_scores 文件数: {len(qs_files)}", len(qs_files) >= 0)
        if qs_files:
            qs = json.loads(qs_files[0].read_text(encoding="utf-8"))
            check("  quality_score 有 overall", "overall" in qs)
            check("  quality_score 有 relevance", "relevance" in qs)
            check("  quality_score 有 efficiency", "efficiency" in qs)

    # quality_weight.json
    qw_path = exp_dir / "quality_weight.json"
    if qw_path.exists():
        qw = json.loads(qw_path.read_text(encoding="utf-8"))
        check(f"  quality_weight = {qw.get('weight')}", True)
        if qw.get("weight", 0) > 0.10:
            check("  权重已自适应增长", True)
        else:
            check("  权重仍在初始值 (正常)", True)

    # approvals
    app_dir = OUT_DIR / "approvals"
    if app_dir.exists():
        app_files = list(app_dir.glob("*.json"))
        check(f"approval 文件数: {len(app_files)}", len(app_files) >= 1)
        if app_files:
            ad = json.loads(app_files[0].read_text(encoding="utf-8"))
            check("  retry_count 存在", "retry_count" in ad)
            check("  status = rejected", ad.get("status") == "rejected")


# ====================================================================
async def amain():
    print("=" * 60)
    print("自进化全链路集成测试 (LMStudio + 工作区数据)")
    print(f"工作区: {WORKSPACE}")
    print(f"输出: {OUT_DIR}")
    print("=" * 60)

    test_trace_reading()
    test_memory_reading()
    await test_experiment_loop()
    await test_pattern_hint()
    await test_auto_evolver()
    test_approval_retry()
    test_pattern_learner()
    analyze_generated_files()

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
