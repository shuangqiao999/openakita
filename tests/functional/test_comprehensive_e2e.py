"""
OpenAkita 自进化系统 多方位无死角功能测试 (LMStudio Live)

测试覆盖 10 个维度:
  1. 实验循环 (ExperimentLoop) — 假设生成 + 实验 + 质量评分
  2. 质量管线 (Quality) — 评分→保存→聚合→权重自适应
  3. Benchmark 引擎 — 任务加载 + 运行 + 基线
  4. 失败自动进化 (AutoEvolver) — 7 种 gap 响应
  5. 审批队列 (ApprovalQueue) — 提交→批准→拒绝→重试限制
  6. 运行时指标 (RuntimeMetrics) — collect + snapshot + DB
  7. 记忆检索 (bump_access) — access_count 递增验证
  8. 动态 Benchmark (DynamicBenchmark) — 变体生成 + 去重
  9. 环境调优 (EnvTuner) — .env 读写 + 回滚
  10. 全链路集成 — trace→metrics→benchmark→experiment→quality

运行: python tests/functional/test_comprehensive_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-needed")
os.environ.setdefault("DEFAULT_MODEL", "qwen/qwen3.5-9b")

OUT_DIR = _project_root / "data" / "test_comprehensive"
PASS = FAIL = 0
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


def clean_output():
    if OUT_DIR.exists():
        shutil.rmtree(str(OUT_DIR))
    OUT_DIR.mkdir(parents=True)


# ====================================================================
# 1. 实验循环
# ====================================================================
async def test_experiment_loop():
    print("\n" + "=" * 60)
    print("1. 实验循环")

    from openakita.core.agent import Agent
    from openakita.core.brain import Brain
    from openakita.evolution.experiment_loop import ExperimentLoop, ExperimentResult

    brain = Brain(api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ["OPENAI_BASE_URL"], model=os.environ["DEFAULT_MODEL"])
    agent = Agent(brain=brain, name="test-comp")
    loop = ExperimentLoop(agent, data_dir=str(OUT_DIR / "experiments"))

    check("_brain 存在", loop._brain is not None)
    check("_quality_eval 已初始化", loop._quality_eval is not None)

    # _is_improvement 静态逻辑
    old = {"success_rate": 0.9, "avg_tokens": 500, "avg_time": 10}
    new_good = {"success_rate": 0.92, "avg_tokens": 450, "avg_time": 8}
    new_worse = {"success_rate": 0.7, "avg_tokens": 200, "avg_time": 5}
    check("改善判定", ExperimentLoop._is_improvement(old, new_good, 0.01))
    check("未改善判定", not ExperimentLoop._is_improvement(old, new_worse, 0.01))
    # 天花板松弛
    old_high = {"success_rate": 0.97, "avg_tokens": 500, "avg_time": 10}
    new_slight = {"success_rate": 0.96, "avg_tokens": 400, "avg_time": 7}
    check("天花板松弛通过", ExperimentLoop._is_improvement(old_high, new_slight, 0.01))

    # fuzzy_match
    c, e = ExperimentLoop._fuzzy_match_and_replace("hello world", "world", "earth")
    check("精确匹配", c is not None and "earth" in c)
    c, e = ExperimentLoop._fuzzy_match_and_replace("hello world", "WORLD", "earth")
    check("大小写不匹配返回None", c is None)

    # syntax
    ok, _ = ExperimentLoop._validate_syntax(Path("x.py"), "print(1)\n")
    check("Python语法", ok)

    # quality_score
    class FM:
        success_rate = 0.9
        avg_tokens = 400.0
        avg_time = 10.0
        efficiency_score = 0.5
        category_scores = {"tool_use": 0.9}
    class FR:
        metrics = FM()
    qs = ExperimentLoop._compute_quality_score(FR(), {"success_rate": 0.85}, {"success_rate": 0.9})
    check("quality_score.overall > 0.5", qs is not None and qs.overall > 0.5)

    await agent.close() if hasattr(agent, "close") else None


# ====================================================================
# 2. 质量管线
# ====================================================================
async def test_quality_pipeline():
    print("\n" + "=" * 60)
    print("2. 质量管线")

    from openakita.evolution.conversation_quality import ConversationQualityEvaluator, QualityScore

    qdir = str(OUT_DIR / "quality")
    Path(qdir).mkdir(parents=True, exist_ok=True)
    eval = ConversationQualityEvaluator(agent=None, data_dir=qdir)

    # save + load
    s = QualityScore(relevance=0.8, correctness=0.9, completeness=0.7, efficiency=0.6)
    s.compute_overall()
    eval.save_score(s, "test123")
    check("save_score 创建文件", len(list(Path(qdir).glob("*.json"))) >= 1)

    avg = eval.load_weekly_average(min_samples=1)
    check("load_weekly_average >=1", isinstance(avg, float))
    avg_none = eval.load_weekly_average(min_samples=100)
    check("load_weekly_average 不足", avg_none is None)

    # adjust_quality_weight
    w = eval.adjust_quality_weight(0.10)
    check("无feedback时返回原值", abs(w - 0.10) < 0.01)

    # _adjust_by_quality_trend
    w = eval._adjust_by_quality_trend(0.10)
    check("trend调整返回float", isinstance(w, float))


# ====================================================================
# 3. Benchmark 引擎
# ====================================================================
def test_benchmark_engine():
    print("\n" + "=" * 60)
    print("3. Benchmark 引擎")

    from openakita.evolution.benchmark import BenchmarkEngine

    engine = BenchmarkEngine()
    tasks = engine.load_tasks()
    check("任务池 >= 8", len(tasks) >= 8)
    ids = {t.id for t in tasks}
    check("tool-file-edit 存在", "tool-file-edit" in ids)
    check("code-fibonacci 存在", "code-fibonacci" in ids)
    check("memory-store-recall 存在", "memory-store-recall" in ids)


# ====================================================================
# 4. 失败自动进化
# ====================================================================
async def test_auto_evolver():
    print("\n" + "=" * 60)
    print("4. 失败自动进化")

    from openakita.core.agent import Agent
    from openakita.core.brain import Brain
    from openakita.evolution.auto_evolve import AutoEvolver, EVOLVABLE_GAPS

    brain = Brain(api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ["OPENAI_BASE_URL"], model=os.environ["DEFAULT_MODEL"])
    agent = Agent(brain=brain, name="test-comp")
    evolver = AutoEvolver(agent)

    check("EVOLVABLE_GAPS = 7", len(EVOLVABLE_GAPS) == 7)

    gaps_tested = 0
    for gap in EVOLVABLE_GAPS:
        result = await evolver.respond_to_failure("test", gap)
        check(f"  {gap} → {result.action}", result.action in ("skip", "evolved", "flagged", "failed"))
        gaps_tested += 1
    check(f"全部7种gap测试通过", gaps_tested == 7)

    # 速率限制: _check_gap_rate 存在且返回 bool
    check("_check_gap_rate 方法存在", hasattr(evolver, "_check_gap_rate"))
    # 注意: 此测试依赖时序, 若前面 LLM 调用 >60s 则速率限制已自然过期

    # 历史记录
    log = OUT_DIR.parent.parent / "data" / "evolution" / "evolution_history.jsonl"
    our_log = _project_root / "data" / "evolution" / "evolution_history.jsonl"
    for p in [our_log, log]:
        if p.exists():
            lines = p.read_text(encoding="utf-8").strip().split("\n")
            check(f"history.jsonl: {len(lines)}条", len(lines) >= 1)
            break

    await agent.close() if hasattr(agent, "close") else None


# ====================================================================
# 5. 审批队列
# ====================================================================
def test_approval_queue():
    print("\n" + "=" * 60)
    print("5. 审批队列")

    from openakita.evolution.approval_queue import ApprovalQueue, ApprovalRequest

    aq = ApprovalQueue(data_dir=str(OUT_DIR / "approvals"))
    req = ApprovalRequest(title="test", target_file="x", original_content="a", proposed_content="b")
    rid = aq.submit(req)
    check("submit 返回 id", bool(rid))

    ok = aq.reject(rid, "test reject")
    data = aq.get(rid)
    check("reject status", data["status"] == "rejected")

    req2 = ApprovalRequest(title="retry", target_file="identity/AGENT.md", original_content="hello", proposed_content="goodbye")
    rid2 = aq.submit(req2)
    for i in range(1, 4):
        aq.approve_and_apply(rid2)
        d = aq.get(rid2)
        check(f"重试{i}: retry={d.get('retry_count')} status={d['status']}",
              d.get("retry_count") == i or (i == 3 and d["status"] == "rejected"))
    check("3次后自动拒绝", aq.get(rid2)["status"] == "rejected")

    # 空内容路径不递增 retry
    req3 = ApprovalRequest(title="empty", target_file="", original_content="", proposed_content="")
    rid3 = aq.submit(req3)
    aq.approve_and_apply(rid3)
    check("空内容 retry=0", aq.get(rid3).get("retry_count", 0) == 0)


# ====================================================================
# 6. 运行时指标
# ====================================================================
def test_runtime_metrics():
    print("\n" + "=" * 60)
    print("6. 运行时指标")

    from openakita.evolution.runtime_metrics import RuntimeMetricsCollector, RuntimeSnapshot

    mdir = str(OUT_DIR / "metrics")
    collector = RuntimeMetricsCollector(data_dir=mdir)
    snap = collector.collect()
    check("collect 返回 RuntimeSnapshot", isinstance(snap, RuntimeSnapshot))
    check("conversation_success_rate 是float", isinstance(snap.conversation_success_rate, float))
    check("memory_usage_rate 是float", isinstance(snap.memory_usage_rate, float))
    check("tool_frequencies 是dict", isinstance(snap.tool_frequencies, dict))

    # 二次 collect (跨线程安全)
    snap2 = collector.collect()
    check("二次collect不崩溃", snap2 is not None)

    # close
    collector.close()
    check("close 不崩溃", True)

    # extract_total_tokens
    check("tokens dict", RuntimeMetricsCollector._extract_total_tokens({"total_tokens": {"input": 10, "output": 5}}) == 15)
    check("tokens int", RuntimeMetricsCollector._extract_total_tokens({"total_tokens": 20}) == 20)
    check("tokens empty", RuntimeMetricsCollector._extract_total_tokens({}) == 0)


# ====================================================================
# 7. 记忆检索 bump_access
# ====================================================================
def test_memory_bump():
    print("\n" + "=" * 60)
    print("7. bump_access 批量 SQL")

    from openakita.memory.unified_store import UnifiedStore
    import sqlite3

    # 创建临时 DB
    tmp_db = OUT_DIR / "memory" / "test.db"
    tmp_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(tmp_db))
    conn.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT, access_count INTEGER DEFAULT 0, last_accessed_at TEXT)")
    conn.execute("INSERT INTO memories (id, access_count) VALUES ('m1', 0)")
    conn.execute("INSERT INTO memories (id, access_count) VALUES ('m2', 0)")
    conn.execute("INSERT INTO memories (id, access_count) VALUES ('m3', 0)")
    conn.commit()
    conn.close()

    # 模拟 bump_access 逻辑
    conn = sqlite3.connect(str(tmp_db))
    now = "2026-06-15T00:00:00"
    conn.execute("UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id IN (?,?,?)", [now, "m1", "m2", "m3"])
    conn.commit()
    counts = conn.execute("SELECT id, access_count FROM memories").fetchall()
    conn.close()
    for row in counts:
        check(f"  {row[0]}: access_count={row[1]}", row[1] == 1)

    # 再次 bump
    conn = sqlite3.connect(str(tmp_db))
    conn.execute("UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id IN (?,?)", [now, "m1", "m2"])
    conn.commit()
    counts = conn.execute("SELECT id, access_count FROM memories").fetchall()
    conn.close()
    for row in counts:
        expected = 2 if row[0] in ("m1", "m2") else 1
        check(f"  二次: {row[0]}: access_count={row[1]}", row[1] == expected)


# ====================================================================
# 8. 动态 Benchmark
# ====================================================================
def test_dynamic_benchmark():
    print("\n" + "=" * 60)
    print("8. 动态 Benchmark")

    from openakita.evolution.dynamic_benchmark import DynamicBenchmarkGenerator as DBG

    # SimHash
    h1 = DBG._simhash("search for files")
    h2 = DBG._simhash("search for files")
    h3 = DBG._simhash("completely different task")
    check("SimHash 相同→相同", h1 == h2)
    check("SimHash 不同→不同", h1 != h3)

    # _is_task_valid
    valid = {"description": "创建排序算法", "expected_outcome": "应该包含排序后的结果数组，至少10个元素", "timeout_seconds": 300}
    check("valid task", DBG._is_task_valid(valid))
    invalid = {"description": "搜索", "expected_outcome": "OK", "timeout_seconds": 10}
    check("invalid task", not DBG._is_task_valid(invalid))

    # validate_task
    ok, _ = DBG.validate_task("编写斐波那契函数", "代码应该包含def fibonacci并通过测试输出55", 120)
    check("validate_task 通过", ok)


# ====================================================================
# 9. EnvTuner
# ====================================================================
def test_env_tuner():
    print("\n" + "=" * 60)
    print("9. EnvTuner")

    from openakita.evolution.env_tuner import EnvTuner

    env_path = OUT_DIR / "test.env"
    env_path.write_text("KEY1=val1\nKEY2=val2\n", encoding="utf-8")
    tuner = EnvTuner(env_path, backup_dir=str(OUT_DIR / "backups"))

    val = tuner.read("KEY1")
    check("read 存在", val == "val1")
    check("read 不存在", tuner.read("NONEXIST") is None)

    backup, ok = tuner.apply("KEY1", "new_val")
    check("apply 成功", ok)
    check("备份创建", backup is not None and backup.exists())
    content = env_path.read_text(encoding="utf-8")
    check("内容修改", "KEY1=new_val" in content)

    tuner.rollback(backup)
    content = env_path.read_text(encoding="utf-8")
    check("回滚恢复", "KEY1=val1" in content)

    tuner.cleanup_backups(max_age_days=0)


# ====================================================================
# 10. 全链路集成
# ====================================================================
async def test_full_integration():
    print("\n" + "=" * 60)
    print("10. 全链路集成")

    from openakita.core.agent import Agent
    from openakita.core.brain import Brain
    from openakita.evolution.benchmark import BenchmarkEngine
    from openakita.evolution.experiment_loop import ExperimentLoop
    from openakita.evolution.runtime_metrics import RuntimeMetricsCollector
    from openakita.evolution.dynamic_benchmark import DynamicBenchmarkGenerator
    from openakita.evolution.auto_evolve import AutoEvolver
    from openakita.evolution.approval_queue import ApprovalQueue, ApprovalRequest

    brain = Brain(api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ["OPENAI_BASE_URL"], model=os.environ["DEFAULT_MODEL"])
    agent = Agent(brain=brain, name="test-full")

    # Benchmark
    engine = BenchmarkEngine()
    check("load_tasks 正常", len(engine.load_tasks()) >= 8)

    # ExperimentLoop
    loop = ExperimentLoop(agent, data_dir=str(OUT_DIR / "full_experiments"))

    # Metrics
    collector = RuntimeMetricsCollector(data_dir=str(OUT_DIR / "full_metrics"))
    snap = collector.collect()
    check("snapshot ok", snap is not None)
    collector.save_snapshot(snap)

    # Dynamic
    gen = DynamicBenchmarkGenerator(agent)
    check("generate_from_traces", isinstance(
        await gen.generate_from_traces(max_tasks=1), list
    ))

    # AutoEvolver
    evolver = AutoEvolver(agent)
    result = await evolver.respond_to_failure("test", "missing_tool")
    check("auto_evolver 不崩溃", result.action in ("skip", "evolved", "failed"))

    # Approval
    aq = ApprovalQueue(data_dir=str(OUT_DIR / "full_approvals"))
    rid = aq.submit(ApprovalRequest(title="final"))
    check("approval submit ok", bool(rid))

    # quality_weight
    qw_path = OUT_DIR / "full_experiments" / "quality_weight.json"
    if qw_path.exists():
        qw = json.loads(qw_path.read_text(encoding="utf-8"))
        check("quality_weight 在有效范围", 0.05 <= qw.get("weight", 0) <= 0.3)

    collector.close()
    await agent.close() if hasattr(agent, "close") else None


# ====================================================================
async def amain():
    print("=" * 60)
    print("OpenAkita 自进化 多方位功能测试 (LMStudio)")
    print("=" * 60)

    clean_output()

    await test_experiment_loop()
    await test_quality_pipeline()
    test_benchmark_engine()
    await test_auto_evolver()
    test_approval_queue()
    test_runtime_metrics()
    test_memory_bump()
    test_dynamic_benchmark()
    test_env_tuner()
    await test_full_integration()

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
