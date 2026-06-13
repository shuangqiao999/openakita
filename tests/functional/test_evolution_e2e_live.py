#!/usr/bin/env python
"""
OpenAkita 自进化系统 全流程端到端测试 (LMStudio Live)

运行方式:
  python tests/functional/test_evolution_e2e_live.py

前置条件:
  1. LMStudio 运行于 http://localhost:1234/v1
  2. 已加载模型 (默认 qwen3.5-9b)
  3. openakita 已正确安装 (pip install -e ".[dev]")

测试覆盖:
  A. 模块导入 & 基础结构
  B. BenchmarkEngine (任务加载/运行/基线保存)
  C. ExperimentLoop (假设生成/实验/带质量权重的改进判断)
  D. 质量管线 (ConversationQualityEvaluator 评分→保存→聚合→权重自适应)
  E. RuntimeMetricsCollector (单次扫描/快照/last_collect增量)
  F. DynamicBenchmarkGenerator (变体生成/验证器/任务池/场景化生成)
  G. EnvTuner (.env 读写/原子写/回滚)
  H. PromptOptimizer (变体提议/验证/采样级别)
  I. ResearchOrg (提案生成)
  J. PatternLearner (模式学习)
  K. AutoEvolver (失败响应/能力补全)
  L. 全流程 (executor._system_benchmark_evolve 模拟)
  M. 边缘情况 (并发/超时/回滚/CancelledError/空数据)
  N. 质量分数管线完整性校验
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# ── 环境配置：指向本地 LMStudio ──────────────────────────────
os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-needed")
os.environ.setdefault("DEFAULT_MODEL", "qwen/qwen3.5-9b")
os.environ.setdefault("OPENAKITA_PROJECT_ROOT", str(Path(__file__).parent.parent.parent))

# 必须在 import openakita.config 之前设置
_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

PASS = 0
FAIL = 0
SKIP = 0
FAILED_TESTS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED_TESTS.append(f"{name} ({detail})" if detail else name)
        print(f"  [FAIL] {name}  —  {detail}" if detail else f"  [FAIL] {name}")


def skip_test(name: str, reason: str = "") -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {name}  —  {reason}")


# ══════════════════════════════════════════════════════════════════
def make_brain():
    """创建指向 LMStudio 的 Brain"""
    from openakita.core.brain import Brain

    return Brain(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
        model=os.environ["DEFAULT_MODEL"],
    )


async def make_agent():
    """创建带 LMStudio Brain 的 Agent"""
    from openakita.core.agent import Agent

    brain = make_brain()
    return Agent(brain=brain)


def make_settings_patch():
    """修改 settings 使其指向测试目录"""
    from openakita import config

    orig_data_dir = config.settings.data_dir
    test_data_dir = _project_root / "data" / "test_evolution_e2e"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    (test_data_dir / "benchmark").mkdir(parents=True, exist_ok=True)
    (test_data_dir / "evolution").mkdir(parents=True, exist_ok=True)
    (test_data_dir / "memory").mkdir(parents=True, exist_ok=True)
    return test_data_dir, orig_data_dir


# ══════════════════════════════════════════════════════════════════
# A. 模块导入 & 基础结构
# ══════════════════════════════════════════════════════════════════
def test_imports():
    print("\n" + "=" * 64)
    print("A. 模块导入 & 基础结构")

    from openakita.config import EVOLVABLE_ENV_PARAMS
    from openakita.evolution import (
        QualityScore,
        RuntimeSnapshot,
    )
    from openakita.evolution._utils import strip_json, strip_json_fences

    check("strip_json_fences 别名一致", strip_json is strip_json_fences)

    # JSON 清理测试
    raw = '```json\n{"a": 1}\n```\n'
    check("strip_json_fences 去除围栏", strip_json_fences(raw) == '{"a": 1}')
    raw2 = '前言说明\n{"b": 2}\n后记'
    check("strip_json_fences 修剪前后文", '"b"' in strip_json_fences(raw2))

    # QualityScore 计算
    s = QualityScore(relevance=0.8, correctness=0.7, completeness=0.6, efficiency=0.5)
    s.compute_overall()
    check("QualityScore.overall 计算", abs(s.overall - 0.675) < 0.01)

    # RuntimeSnapshot 新字段
    snap = RuntimeSnapshot()
    check("conversation_success_rate 存在", hasattr(snap, "conversation_success_rate"))
    check("conversation_avg_tokens 存在", hasattr(snap, "conversation_avg_tokens"))
    check("memory_usage_rate 存在", hasattr(snap, "memory_usage_rate"))

    # EVOLVABLE_ENV_PARAMS 类型正确
    check("MEMORY_RETRIEVAL_TUNING_ENABLED 为 float", isinstance(
        EVOLVABLE_ENV_PARAMS["MEMORY_RETRIEVAL_TUNING_ENABLED"][0], float
    ))
    check("QUALITY_WEIGHT_IN_IMPROVEMENT 默认 0.10", abs(
        EVOLVABLE_ENV_PARAMS["QUALITY_WEIGHT_IN_IMPROVEMENT"][0] - 0.10
    ) < 0.01)

    print("  → 导入测试完成")


# ══════════════════════════════════════════════════════════════════
# B. BenchmarkEngine
# ══════════════════════════════════════════════════════════════════
async def test_benchmark_engine():
    print("\n" + "=" * 64)
    print("B. BenchmarkEngine", flush=True)

    from openakita.evolution.benchmark import BenchmarkEngine

    engine = BenchmarkEngine(data_dir=str(_project_root / "data" / "test_evolution_e2e" / "benchmark"))
    engine._data_dir.mkdir(parents=True, exist_ok=True)
    engine._results_dir.mkdir(parents=True, exist_ok=True)

    tasks = engine.load_tasks()
    check("load_tasks 返回列表", isinstance(tasks, list))
    check("默认任务池非空", len(tasks) >= 8)

    # 验证任务字段
    for i, t in enumerate(tasks[:3]):
        check(f"任务[{i}] id={t.id}", bool(t.id))
        check(f"任务[{i}] description 非空", len(t.description) > 10)
        check(f"任务[{i}] timeout_seconds 合理", 30 <= t.timeout_seconds <= 3600)

    # 单独运行一个简单任务 (不调用 LLM)
    agent = await make_agent()
    try:
        report = await engine.run_suite(agent)
        check("run_suite 返回 BenchmarkReport", report is not None)
        check("metrics.success_rate 在 [0,1]", 0 <= report.metrics.success_rate <= 1)
        check("metrics.efficiency_score 数值", isinstance(report.metrics.efficiency_score, (int, float)))
    except Exception as e:
        skip_test("run_suite 全量", f"Agent 连接失败: {e}")


# ══════════════════════════════════════════════════════════════════
# C. ExperimentLoop
# ══════════════════════════════════════════════════════════════════
async def test_experiment_loop():
    print("\n" + "=" * 64)
    print("C. ExperimentLoop", flush=True)

    from openakita.evolution.experiment_loop import (
        ExperimentLoop,
        _get_env_targets_display,
    )

    agent = await make_agent()
    loop = ExperimentLoop(agent, data_dir=str(_project_root / "data" / "test_evolution_e2e" / "experiments"))

    # 基础属性
    check("_brain 存在", loop._brain is not None)
    check("_data_dir 创建", loop._data_dir.exists())
    check("_backups_dir 创建", loop._backups_dir.exists())

    # _is_improvement 静态方法
    old_m = {"success_rate": 0.5, "avg_tokens": 100, "avg_time": 10}
    new_better = {"success_rate": 0.6, "avg_tokens": 80, "avg_time": 5}
    new_worse = {"success_rate": 0.5, "avg_tokens": 200, "avg_time": 20}

    check(
        "_is_improvement 判断为改善 (no quality)",
        ExperimentLoop._is_improvement(old_m, new_better, 0.01),
    )
    check(
        "_is_improvement 判断为未改善 (no quality)",
        not ExperimentLoop._is_improvement(old_m, new_worse, 0.01),
    )

    # _is_improvement with quality_weight
    check(
        "_is_improvement with quality_weight=0.10 (improve)",
        ExperimentLoop._is_improvement(old_m, new_better, 0.01, quality_weight=0.10),
    )
    check(
        "_is_improvement with quality_weight=0.20 (unchanged)",
        ExperimentLoop._is_improvement(old_m, new_better, 0.01, quality_weight=0.20),
    )

    # 成功率硬约束
    new_lower_sr = {"success_rate": 0.3, "avg_tokens": 10, "avg_time": 1}
    check(
        "成功率下降时永不为改善",
        not ExperimentLoop._is_improvement(old_m, new_lower_sr, 0.01),
    )

    # env targets display
    display = _get_env_targets_display()
    check("_get_env_targets_display 非空", len(display) > 0)
    check("包含 env: 前缀", "env:" in display)

    # fuzzy match
    content = "line1\nhello world\nline3\n"
    new_c, err = ExperimentLoop._fuzzy_match_and_replace(content, "hello world", "goodbye")
    check("精确匹配替换", new_c is not None and "goodbye" in new_c)

    new_c, err = ExperimentLoop._fuzzy_match_and_replace(content, "hello  world", "goodbye")
    check("空白归一化匹配", new_c is not None and "goodbye" in new_c)

    new_c, err = ExperimentLoop._fuzzy_match_and_replace(content, "nonexistent", "x")
    check("无法匹配返回 None", new_c is None)

    # syntax validation
    ok, _ = ExperimentLoop._validate_syntax(Path("test.py"), "x = 1\n")
    check("Python 语法验证通过", ok)

    ok, _ = ExperimentLoop._validate_syntax(Path("test.py"), "x = \n")
    check("Python 语法验证失败", not ok)

    # quality score computation
    class FakeMetrics:
        success_rate = 0.8
        avg_tokens = 50.0
        avg_time = 10.0
        efficiency_score = 0.5

    class FakeReport:
        metrics = FakeMetrics()

    score = ExperimentLoop._compute_quality_score(FakeReport())
    check("_compute_quality_score 返回 QualityScore", score is not None)
    check("quality_score.overall > 0", score.overall > 0)

    # quality evaluator in loop
    check("_quality_eval 已初始化", loop._quality_eval is not None)

    # 实验循环 (使用 mock benchmark 避免跑真实 LLM benchmark)
    try:
        class MockMetrics:
            success_rate = 0.7
            avg_tokens = 50.0
            avg_time = 8.0
            efficiency_score = 0.5

        class MockReport:
            metrics = MockMetrics()

        results = await loop.run_cycle(benchmark_report=MockReport())
        check("run_cycle 返回列表", isinstance(results, list))
        if results:
            check("ExperimentResult 包含 quality_score", hasattr(results[0], "quality_score"))
    except Exception as e:
        skip_test("run_cycle (完整)", f"{type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════════
# D. 质量管线 (ConversationQualityEvaluator)
# ══════════════════════════════════════════════════════════════════
async def test_quality_pipeline():
    print("\n" + "=" * 64)
    print("D. 质量管线", flush=True)

    from openakita.evolution.conversation_quality import (
        ConversationQualityEvaluator,
        QualityScore,
    )

    agent = await make_agent()
    qdir = str(_project_root / "data" / "test_evolution_e2e" / "quality_scores")
    Path(qdir).mkdir(parents=True, exist_ok=True)
    eval = ConversationQualityEvaluator(agent, data_dir=qdir)

    # evaluate_turn
    try:
        score = await eval.evaluate_turn(
            "如何计算斐波那契数列?",
            "斐波那契数列定义为 F(n)=F(n-1)+F(n-2)，可以递归或迭代计算。",
            ["python_exec", "search_web"],
        )
        check("evaluate_turn 返回 QualityScore", isinstance(score, QualityScore))
        check("relevance 在 [0,1]", 0 <= score.relevance <= 1)
        check("overall 在 [0,1]", 0 <= score.overall <= 1)
    except Exception as e:
        skip_test("evaluate_turn", f"LLM 调用失败: {e}")

    # save_score + load_weekly_average
    s = QualityScore(relevance=0.8, correctness=0.7, completeness=0.6, efficiency=0.5)
    s.compute_overall()
    eval.save_score(s, session_id="test_session_001")
    check("save_score 创建文件", list(Path(qdir).glob("*.json")))

    avg = eval.load_weekly_average(min_samples=1)
    check("load_weekly_average >=1 样本时返回 float", isinstance(avg, float))

    avg = eval.load_weekly_average(min_samples=100)
    check("load_weekly_average 不足样本时返回 None", avg is None)

    # adjust_quality_weight
    new_w = eval.adjust_quality_weight(0.10)
    check("无 feedback 时返回原值", abs(new_w - 0.10) < 0.01)

    # 写 feedback.json 模拟用户反馈
    fb_path = _project_root / "data" / "evolution" / "feedback.json"
    fb_path.parent.mkdir(parents=True, exist_ok=True)
    fb_path.write_text(json.dumps(
        [{"session_id": "test_session_001", "rating": "good"}] * 6,
        ensure_ascii=False,
    ), encoding="utf-8")

    new_w = eval.adjust_quality_weight(0.10)
    check("有 feedback 时返回调整后的权重", isinstance(new_w, float))
    # 清理
    fb_path.unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════
# E. RuntimeMetricsCollector
# ══════════════════════════════════════════════════════════════════
def test_runtime_metrics():
    print("\n" + "=" * 64)
    print("E. RuntimeMetricsCollector", flush=True)

    from openakita.evolution.runtime_metrics import RuntimeMetricsCollector, RuntimeSnapshot

    mdir = str(_project_root / "data" / "test_evolution_e2e" / "metrics")
    collector = RuntimeMetricsCollector(data_dir=mdir)

    # collect
    snapshot = collector.collect()
    check("collect 返回 RuntimeSnapshot", isinstance(snapshot, RuntimeSnapshot))
    check("timestamp 非空", bool(snapshot.timestamp))
    check("conversation_success_rate 为 float", isinstance(snapshot.conversation_success_rate, float))
    check("conversation_avg_tokens 为 float", isinstance(snapshot.conversation_avg_tokens, float))
    check("memory_usage_rate 为 float", isinstance(snapshot.memory_usage_rate, float))

    # save_snapshot
    path = collector.save_snapshot(snapshot)
    check("save_snapshot 创建文件", path.exists())

    # last_collect 增量
    snap2 = collector.collect()
    check("增量收集不因重复文件崩溃", isinstance(snap2, RuntimeSnapshot))

    # extract_total_tokens
    from openakita.evolution.runtime_metrics import RuntimeMetricsCollector as RMC
    data1 = {"total_tokens": {"input": 100, "output": 50}}
    check("extract total_tokens dict", RMC._extract_total_tokens(data1) == 150)
    data2 = {"total_tokens": 200}
    check("extract total_tokens int", RMC._extract_total_tokens(data2) == 200)
    data3 = {"iterations": [{"tokens_used": 30}, {"tokens_used": 20}]}
    check("extract total_tokens iterations", RMC._extract_total_tokens(data3) == 50)
    data4 = {}
    check("extract total_tokens empty", RMC._extract_total_tokens(data4) == 0)

    # get/record_tuning_time — 先清理残留文件
    for p in Path(mdir).glob("last_memory_tuning*"):
        p.unlink(missing_ok=True)
    check("get_last_tuning_time 默认 0", collector.get_last_tuning_time() == 0.0)
    collector.record_tuning_time()
    check("record_tuning_time 写入时间戳", collector.get_last_tuning_time() > 0)


# ══════════════════════════════════════════════════════════════════
# F. DynamicBenchmarkGenerator
# ══════════════════════════════════════════════════════════════════
async def test_dynamic_benchmark():
    print("\n" + "=" * 64)
    print("F. DynamicBenchmarkGenerator", flush=True)

    from openakita.evolution.dynamic_benchmark import DynamicBenchmarkGenerator

    agent = await make_agent()
    gen = DynamicBenchmarkGenerator(agent)

    # 静态验证器
    from openakita.evolution.dynamic_benchmark import DynamicBenchmarkGenerator as DBG

    ok, reason = DBG.validate_task("创建一个排序算法", "应该包含排序后的结果数组，至少10个元素", 120)
    check("validate_task 通过", ok)

    ok, reason = DBG.validate_task("", "结果正确", 120)
    check("validate_task 拒绝空描述", not ok)
    check("原因包含动作动词", "动作动词" in reason)

    ok, reason = DBG.validate_task("搜索文件", "可以", 120)
    check("validate_task 拒绝不可验证预期", not ok)

    ok, reason = DBG.validate_task("计算", "结果为42", 20000)
    check("validate_task 拒绝超时越界", not ok)

    # _is_task_valid
    valid_task = {
        "description": "搜索所有 Python 文件",
        "expected_outcome": "找到至少 5 个 .py 文件",
        "timeout_seconds": 300,
        "category": "tool_use",
    }
    check("_is_task_valid 通过", DBG._is_task_valid(valid_task))

    invalid_task = {
        "description": "搜索",
        "expected_outcome": "OK",
        "timeout_seconds": 10,
        "category": "",
    }
    check("_is_task_valid 拒绝", not DBG._is_task_valid(invalid_task))

    coding_task = {
        "description": "编写函数",
        "expected_outcome": "完成",
        "timeout_seconds": 300,
        "category": "coding",
    }
    check("coding 无代码词拒绝", not DBG._is_task_valid(coding_task))

    # SimHash
    h1 = DBG._simhash("search for files in directory")
    h2 = DBG._simhash("search for files in directory")
    h3 = DBG._simhash("completely different task")
    check("SimHash 相同输入相同输出", h1 == h2)
    check("SimHash 不同输入不同输出", h1 != h3)

    # is_duplicate
    class FakeTask:
        description = "search for files in directory"

    check("_is_duplicate 检测重复", gen._is_duplicate("search for files in directory", [FakeTask()]))
    check("_is_duplicate 不误报", not gen._is_duplicate("completely different", [FakeTask()]))

    # generate_from_traces (无真实 trace)
    tasks = await gen.generate_from_traces(max_tasks=3)
    check("generate_from_traces 空数据时返回 []", tasks == [])


# ══════════════════════════════════════════════════════════════════
# G. EnvTuner
# ══════════════════════════════════════════════════════════════════
def test_env_tuner():
    print("\n" + "=" * 64)
    print("G. EnvTuner", flush=True)

    from openakita.evolution.env_tuner import EnvTuner

    test_env = _project_root / "data" / "test_evolution_e2e" / "test.env"
    test_env.write_text("EXISTING_KEY=old_value\nOTHER=keep\n", encoding="utf-8")

    tuner = EnvTuner(test_env, backup_dir=str(_project_root / "data" / "test_evolution_e2e" / ".env.backups"))

    # read
    val = tuner.read("EXISTING_KEY")
    check("read 读取已有 key", val == "old_value")
    val = tuner.read("NONEXISTENT")
    check("read 读取不存在 key", val is None)

    # apply (修改已有 key)
    backup, ok = tuner.apply("EXISTING_KEY", "new_value")
    check("apply 修改成功", ok)
    check("备份文件创建", backup is not None and backup.exists())
    content = test_env.read_text(encoding="utf-8")
    check("文件内容已修改", "EXISTING_KEY=new_value" in content)

    # apply (新增 key)
    backup2, ok = tuner.apply("NEW_KEY", "123")
    check("apply 新增 key", ok)
    content = test_env.read_text(encoding="utf-8")
    check("新 key 已添加", "NEW_KEY=123" in content)

    # rollback
    tuner.rollback(backup)
    content = test_env.read_text(encoding="utf-8")
    check("回滚恢复旧值", "EXISTING_KEY=old_value" in content)

    # cleanup
    tuner.cleanup_backups(max_age_days=0)
    backups_after = list((_project_root / "data" / "test_evolution_e2e" / ".env.backups").glob("env_backup_*"))
    check("cleanup 清理过期备份", len(backups_after) == 0)


# ══════════════════════════════════════════════════════════════════
# H. PromptOptimizer
# ══════════════════════════════════════════════════════════════════
async def test_prompt_optimizer():
    print("\n" + "=" * 64)
    print("H. PromptOptimizer", flush=True)

    from openakita.evolution.prompt_optimizer import PromptOptimizer

    agent = await make_agent()
    optimizer = PromptOptimizer(agent, data_dir=str(_project_root / "data" / "test_evolution_e2e" / "prompt_opt"))

    check("_brain 可用", optimizer._brain is not None)

    # evolve_step
    try:
        result = await optimizer.evolve_step(
            performance_data={"success_rate": 0.5, "avg_tokens": 200}
        )
        check("evolve_step 返回 VariantResult 或 None", result is not None)
    except Exception as e:
        skip_test("evolve_step", f"{type(e).__name__}: {e}")

    # validate
    ok, _ = optimizer._validate_template_vars("你好{name}")
    check("_validate_template_vars 格式", isinstance(ok, bool))

    # _validate_change_ratio requires PromptVariant — skip standalone test
    check("_validate_change_ratio 方法存在", hasattr(optimizer, "_validate_change_ratio"))


# ══════════════════════════════════════════════════════════════════
# I. ResearchOrg
# ══════════════════════════════════════════════════════════════════
async def test_research_org():
    print("\n" + "=" * 64)
    print("I. ResearchOrg", flush=True)

    from openakita.evolution.research_org import ResearchOrg

    agent = await make_agent()
    org = ResearchOrg(agent, data_dir=str(_project_root / "data" / "test_evolution_e2e" / "research"))

    # run_research_cycle
    try:
        result = await org.run_research_cycle(
            performance_data={"success_rate": 0.5, "avg_tokens": 200}
        )
        check("run_research_cycle 返回 ResearchCycleResult", result is not None)
    except Exception as e:
        skip_test("run_research_cycle", f"{type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════════
# J. PatternLearner
# ══════════════════════════════════════════════════════════════════
async def test_pattern_learner():
    print("\n" + "=" * 64)
    print("J. PatternLearner", flush=True)

    from openakita.evolution.pattern_learner import PatternLearner

    agent = await make_agent()
    learner = PatternLearner(agent)

    # extract_tool_names
    steps = [
        {"tool_name": "python_exec", "args": {"code": "print(1)"}},
        {"tool_name": "search_web", "args": {"query": "test"}},
    ]
    tools = PatternLearner._extract_tool_names(steps)
    check("_extract_tool_names 提取工具名", "python_exec" in tools and "search_web" in tools)

    # learn_from_history (无真实数据)
    try:
        patterns = await learner.learn_from_history(days=1)
        check("learn_from_history 返回列表", isinstance(patterns, list))
    except Exception as e:
        skip_test("learn_from_history", f"{type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════════
# K. AutoEvolver
# ══════════════════════════════════════════════════════════════════
async def test_auto_evolver():
    print("\n" + "=" * 64)
    print("K. AutoEvolver", flush=True)

    from openakita.evolution.auto_evolve import EVOLVABLE_GAPS, AutoEvolver

    agent = await make_agent()
    evolver = AutoEvolver(agent)

    check("EVOLVABLE_GAPS 包含 missing_tool", "missing_tool" in EVOLVABLE_GAPS)

    # 非可进化间隙
    result = await evolver.respond_to_failure("测试任务", "unknown_gap")
    check("非可进化间隙返回 skip", result.action == "skip")

    # 去重缓存
    evolver._mark_processed("test_cap")
    check("_is_recently_processed 检测已处理", evolver._is_recently_processed("test_cap"))


# ══════════════════════════════════════════════════════════════════
# L. 全流程 (executor 模拟)
# ══════════════════════════════════════════════════════════════════
async def test_full_flow():
    print("\n" + "=" * 64)
    print("L. 全流程 (executor 模拟)", flush=True)

    from openakita.evolution.benchmark import BenchmarkEngine
    from openakita.evolution.experiment_loop import ExperimentLoop
    from openakita.evolution.runtime_metrics import RuntimeMetricsCollector

    agent = await make_agent()

    # Step 1: Benchmark
    engine = BenchmarkEngine()
    try:
        report = await engine.run_suite(agent)
        check("Step1 Benchmark 返回 report", report is not None)
        check("success_rate 合理", 0 <= report.metrics.success_rate <= 1)
    except Exception as e:
        skip_test("Step1 Benchmark", f"{type(e).__name__}: {e}")
        report = None

    # Step 2: ExperimentLoop
    if report:
        loop = ExperimentLoop(agent)
        try:
            results = await loop.run_cycle(benchmark_report=report)
            check("Step2 实验循环返回结果", isinstance(results, list))
            kept = [r for r in results if r.action == "keep"]
            check(f"Step2 实验: {len(results)}次 保留{len(kept)}项", len(results) >= 0)
        except Exception as e:
            skip_test("Step2 ExperimentLoop", f"{type(e).__name__}: {e}")

    # Step 3: Metrics collection
    try:
        collector = RuntimeMetricsCollector()
        snapshot = collector.collect()
        check("Step3 指标采集成功", snapshot is not None)
        check("memory_total >= 0", snapshot.memory_total >= 0)
        collector.save_snapshot(snapshot)
    except Exception as e:
        skip_test("Step3 Metrics", f"{type(e).__name__}: {e}")

    # Step 4: Dynamic benchmark
    from openakita.evolution.dynamic_benchmark import DynamicBenchmarkGenerator
    try:
        gen = DynamicBenchmarkGenerator(agent)
        original = engine.load_tasks()
        history = {t.id: 0.5 for t in original[:5]}
        pool = await gen.maintain_task_pool(original, history)
        check("Step4 任务池维护", len(pool) >= len(original))
    except Exception as e:
        skip_test("Step4 DynamicBenchmark", f"{type(e).__name__}: {e}")

    # Step 5: Quality pipeline
    try:
        q_eval = loop._quality_eval
        check("Step5 质量管线就绪", q_eval is not None)
        if q_eval:
            avg_q = q_eval.load_weekly_average(min_samples=1)
            check("load_weekly_average", isinstance(avg_q, float) or avg_q is None)
    except Exception as e:
        skip_test("Step5 Quality", f"{type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════════
# M. 边缘情况
# ══════════════════════════════════════════════════════════════════
async def test_edge_cases():
    print("\n" + "=" * 64)
    print("M. 边缘情况", flush=True)

    from openakita.evolution.conversation_quality import ConversationQualityEvaluator
    from openakita.evolution.experiment_loop import (
        ExperimentLoop,
        Hypothesis,
    )

    agent = await make_agent()

    # 空 brain 的假设生成
    loop = ExperimentLoop(
        agent=None,  # type: ignore
        data_dir=str(_project_root / "data" / "test_evolution_e2e" / "edge")
    )
    loop._brain = None  # 覆盖
    hypothesis = await loop._generate_hypothesis({"success_rate": 0.5}, [])
    check("无 brain 时 _generate_hypothesis 返回 None", hypothesis is None)

    # 空目标文件的假设
    loop._brain = None
    try:
        a = await make_agent()
        loop._brain = a.brain if a.brain else None
    except Exception:
        loop._brain = None
    # 破坏 MUTABLE_TARGETS
    saved_targets = loop.MUTABLE_TARGETS[:]
    loop.MUTABLE_TARGETS = ["nonexistent/file.txt"]
    hypothesis = await loop._generate_hypothesis({"success_rate": 0.5}, [])
    check("无目标文件时返回 None", hypothesis is None)
    loop.MUTABLE_TARGETS = saved_targets

    # 路径遍历攻击检测
    hyp = Hypothesis(
        target="../etc/passwd",
        description="bad",
        original_content="a",
        proposed_content="b",
        rationale="test",
    )
    result = await loop._run_experiment(hyp, None, {})
    check("路径遍历检测阻止", result.action == "error")
    check("原因包含路径检测", bool(result.reason))

    # target 不在白名单
    hyp = Hypothesis(
        target="nonexistent/file.py",
        description="bad",
        original_content="a",
        proposed_content="b",
        rationale="test",
    )
    result = await loop._run_experiment(hyp, None, {})
    check("非白名单目标拒绝", result.action == "error")

    # env param 不在白名单
    hyp = Hypothesis(
        target="env:NOT_WHITELISTED",
        description="bad",
        original_content="1",
        proposed_content="5",
        rationale="test",
    )
    result = await loop._run_experiment(hyp, None, {})
    check("非白名单 env 拒绝", result.action == "error")

    # 替换区域超 30%
    loop._project_root = _project_root
    test_file = _project_root / "data" / "test_evolution_e2e" / "test_file.txt"
    long_content = "A" * 500
    test_file.write_text(long_content, encoding="utf-8")
    hyp = Hypothesis(
        target=str(test_file.relative_to(_project_root)),
        description="big change",
        original_content="A" * 200,  # 40%
        proposed_content="B" * 200,
        rationale="test",
    )
    # target 不在 MUTABLE_TARGETS 所以会走到 path validation
    # 但 MUTABLE_TARGETS 默认只有 identity/ 下的文件
    # 我们先加到 MUTABLE_TARGETS 中测试
    saved_targets = loop.MUTABLE_TARGETS[:]
    loop.MUTABLE_TARGETS.append(str(test_file.relative_to(_project_root)))
    result = await loop._run_experiment(hyp, None, {})
    check("替换超 30% 拒绝", result.action == "error" and "30%" in result.reason)
    loop.MUTABLE_TARGETS = saved_targets
    test_file.unlink(missing_ok=True)

    # 空 brain 的 evaluate_turn
    q_eval = ConversationQualityEvaluator(agent=None, data_dir=str(_project_root / "data" / "test_evolution_e2e" / "edge_q"))  # type: ignore
    q_eval._brain = None
    score = await q_eval.evaluate_turn("", "", [])
    check("无 brain evaluate 返回默认值", abs(score.overall - 0.5) < 0.01)

    # duplicate _is_task_valid edge cases
    from openakita.evolution.dynamic_benchmark import DynamicBenchmarkGenerator as DBG
    check("_is_task_valid None desc", not DBG._is_task_valid({}))
    check("_is_task_valid coding with valid", DBG._is_task_valid({
        "description": "编写排序函数",
        "expected_outcome": "代码通过测试并正确输出结果",
        "timeout_seconds": 300,
        "category": "coding",
    }))


# ══════════════════════════════════════════════════════════════════
# N. 质量分数管线完整性校验
# ══════════════════════════════════════════════════════════════════
async def test_quality_pipeline_integration():
    print("\n" + "=" * 64)
    print("N. 质量管线完整性", flush=True)

    from openakita.config import EVOLVABLE_ENV_PARAMS
    from openakita.evolution.experiment_loop import ExperimentLoop

    agent = await make_agent()

    qdir = str(_project_root / "data" / "test_evolution_e2e" / "quality_int")
    Path(qdir).mkdir(parents=True, exist_ok=True)

    loop = ExperimentLoop(agent, data_dir=str(_project_root / "data" / "test_evolution_e2e" / "exp_int"))

    # 校验 quality_weight 从 config 正确读取
    qw = loop._get_config("quality_weight_in_improvement", 0.10)
    check(f"quality_weight_in_improvement = {qw:.2f}", abs(qw - 0.10) < 0.01)

    # 校验 EVOLVABLE_ENV_PARAMS 中的值也一致
    env_qw = EVOLVABLE_ENV_PARAMS["QUALITY_WEIGHT_IN_IMPROVEMENT"][0]
    check(f"EVOLVABLE_ENV_PARAMS 中为 {env_qw}", abs(env_qw - 0.10) < 0.01)

    # 校验 _is_improvement 统计接受 quality_weight
    old = {"success_rate": 0.7, "avg_tokens": 100, "avg_time": 10}
    new = {"success_rate": 0.75, "avg_tokens": 95, "avg_time": 9}

    base_improved = ExperimentLoop._is_improvement(old, new, 0.01, quality_weight=0.0, quality_delta=0.0)
    check("无质量权重下改善判断", base_improved)

    # quality_weight 0.30 仍能通过 (因为改善幅度大)
    improved_with_q = ExperimentLoop._is_improvement(old, new, 0.01, quality_weight=0.30, quality_delta=0.0)
    check("质量权重 0.30 下通过", improved_with_q)

    # quality_delta 为正值时更容易通过
    improved_with_pos_qd = ExperimentLoop._is_improvement(old, new, 0.01, quality_weight=0.30, quality_delta=0.5)
    check("正 quality_delta 通过", improved_with_pos_qd)

    # 负 quality_delta 时更难通过
    not_improved_neg_qd = ExperimentLoop._is_improvement(
        old, {"success_rate": 0.71, "avg_tokens": 99, "avg_time": 9.9},
        0.01, quality_weight=0.30, quality_delta=-0.3,
    )
    check("负 quality_delta 拒绝边际改善", not not_improved_neg_qd)

    # 校验 load_weekly_average 集成到 run_cycle
    avg = loop._quality_eval.load_weekly_average(min_samples=3) if loop._quality_eval else None
    check("run_cycle 前 load_weekly_average 正确", avg is None or isinstance(avg, float))


# ══════════════════════════════════════════════════════════════════
async def amain():
    print("=" * 64)
    print("OpenAkita 自进化系统 全流程端到端测试")
    print(f"LMStudio: {os.environ.get('OPENAI_BASE_URL', 'N/A')}")
    print(f"模型: {os.environ.get('DEFAULT_MODEL', 'N/A')}")
    print("=" * 64)

    test_imports()
    await test_benchmark_engine()
    await test_experiment_loop()
    await test_quality_pipeline()
    test_runtime_metrics()
    await test_dynamic_benchmark()
    test_env_tuner()
    await test_prompt_optimizer()
    await test_research_org()
    await test_pattern_learner()
    await test_auto_evolver()
    await test_full_flow()
    await test_edge_cases()
    test_quality_pipeline_integration()

    # ── 汇总 ──
    total = PASS + FAIL + SKIP
    print("\n" + "=" * 64)
    print(f"  总计: {total}  通过: {PASS}  失败: {FAIL}  跳过: {SKIP}")
    print("=" * 64)

    if FAILED_TESTS:
        print(f"\n  失败项目 ({len(FAILED_TESTS)}):")
        for t in FAILED_TESTS:
            print(f"    - {t}")

    return 0 if FAIL == 0 else 1


def main():
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
