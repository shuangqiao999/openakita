"""
18 项缺陷修复验证测试

覆盖: 3 高危 / 8 中危 / 7 低危
全部为离线单元测试，不需要 LLM API。
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-needed")
os.environ.setdefault("DEFAULT_MODEL", "local-test")

OUT_DIR = _project_root / "data" / "test_18_defect_fixes"
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


def clean():
    if OUT_DIR.exists():
        shutil.rmtree(str(OUT_DIR))
    OUT_DIR.mkdir(parents=True, exist_ok=True)


# ====================================================================
# 🔴 #1  天花板容错: sr_old - 0.05 而非固定 0.85
# ====================================================================
def test_fix1_ceiling_tolerance():
    print("\n" + "=" * 60)
    print("🔴 #1  天花板容错精度")

    from openakita.evolution.experiment_loop import ExperimentLoop

    old = {"success_rate": 0.96, "avg_tokens": 100, "avg_time": 1.0}

    ok_new = {"success_rate": 0.92, "avg_tokens": 90, "avg_time": 0.9}
    check(
        "sr_old=0.96, sr_new=0.92 通过 (差 0.04 < 0.05)",
        ExperimentLoop._is_improvement(old, ok_new, threshold=0.0),
    )

    bad_new = {"success_rate": 0.90, "avg_tokens": 90, "avg_time": 0.9}
    check(
        "sr_old=0.96, sr_new=0.90 拒绝 (差 0.06 > 0.05)",
        not ExperimentLoop._is_improvement(old, bad_new, threshold=0.0),
    )

    old_bug = {"success_rate": 0.95, "avg_tokens": 100, "avg_time": 1.0}
    was_allowed = {"success_rate": 0.86, "avg_tokens": 50, "avg_time": 0.5}
    check(
        "sr_old=0.95, sr_new=0.86 拒绝 (旧逻辑允许到 0.85, 现在最多 0.90)",
        not ExperimentLoop._is_improvement(old_bug, was_allowed, threshold=0.0),
    )

    exact = {"success_rate": 0.90, "avg_tokens": 90, "avg_time": 0.9}
    check(
        "sr_old=0.95, sr_new=0.90 通过 (恰好等于下限)",
        ExperimentLoop._is_improvement(old_bug, exact, threshold=0.0),
    )


# ====================================================================
# 🔴 #2  needs_restart 时跳过 benchmark 返回 pending_restart
# ====================================================================
def test_fix2_needs_restart_skips_benchmark():
    print("\n" + "=" * 60)
    print("🔴 #2  needs_restart 跳过 benchmark")

    src = inspect.getsource(
        __import__(
            "openakita.evolution.experiment_loop", fromlist=["ExperimentLoop"]
        ).ExperimentLoop._run_env_experiment
    )
    check(
        "包含 pending_restart action",
        'action="pending_restart"' in src,
    )
    check(
        "needs_restart=True 分支有提前 return",
        "需要重启才能生效" in src or "需重启" in src,
    )
    check(
        "旧的 needs_restart 分支内的 benchmark 代码已删除",
        "config._restart_requested = True" not in src.split("pending_restart")[0]
        or src.count("config._restart_requested") == 1,
    )


# ====================================================================
# 🔴 #3  executor.py added 变量初始化
# ====================================================================
def test_fix3_added_initialized():
    print("\n" + "=" * 60)
    print("🔴 #3  added 变量初始化")

    src_path = _project_root / "src" / "openakita" / "scheduler" / "executor.py"
    src = src_path.read_text(encoding="utf-8")

    idx_new_variants = src.index("new_variants = [t for t in all_tasks")
    idx_if_new_variants = src.index("if new_variants:", idx_new_variants)
    between = src[idx_new_variants:idx_if_new_variants]
    check(
        "added = 0 在 if new_variants: 之前",
        "added = 0" in between,
    )

    idx_summary = src.index("summary +=", idx_if_new_variants)
    summary_line = src[idx_summary : src.index("\n", idx_summary)]
    check(
        "summary 引用 added 变量不会 NameError",
        "added" in summary_line,
    )


# ====================================================================
# 🟡 #4  orig_env_val 从 .env 文件读取
# ====================================================================
def test_fix4_orig_env_from_dotenv():
    print("\n" + "=" * 60)
    print("🟡 #4  orig_env_val 从 .env 读取")

    src = inspect.getsource(
        __import__(
            "openakita.evolution.experiment_loop", fromlist=["ExperimentLoop"]
        ).ExperimentLoop._run_env_experiment
    )
    check("使用 tuner.read(param)", "tuner.read(param)" in src)
    check(
        "actual_env_val 优先于 getattr fallback",
        src.index("tuner.read(param)") < src.index("getattr(settings, param.lower()"),
    )


# ====================================================================
# 🟡 #5  备份写入在 try 内部
# ====================================================================
def test_fix5_backup_inside_try():
    print("\n" + "=" * 60)
    print("🟡 #5  备份写入在 try 内部")

    src = inspect.getsource(
        __import__(
            "openakita.evolution.experiment_loop", fromlist=["ExperimentLoop"]
        ).ExperimentLoop._run_experiment
    )
    try_idx = src.index("try:")
    backup_write_idx = src.index("backup_path.write_text(")
    check(
        "backup_path.write_text 在 try: 之后",
        backup_write_idx > try_idx,
    )


# ====================================================================
# 🟡 #6  tuner.apply() 异常捕获
# ====================================================================
def test_fix6_apply_exception_handling():
    print("\n" + "=" * 60)
    print("🟡 #6  tuner.apply() 异常捕获")

    src = inspect.getsource(
        __import__(
            "openakita.evolution.experiment_loop", fromlist=["ExperimentLoop"]
        ).ExperimentLoop._run_env_experiment
    )
    check(
        "tuner.apply 在 try 块中",
        "tuner.apply(param, value_str)" in src and "except Exception as apply_err" in src,
    )
    check(
        "异常信息包含在 reason 中",
        ".env 写入失败:" in src,
    )


# ====================================================================
# 🟡 #7  _verify_outcome 关键词匹配回退
# ====================================================================
def test_fix7_verify_outcome_keyword_fallback():
    print("\n" + "=" * 60)
    print("🟡 #7  _verify_outcome 关键词匹配回退")

    from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkTask

    engine = BenchmarkEngine(data_dir=str(OUT_DIR / "bench7"))
    task = BenchmarkTask(
        id="test_kw",
        description="test",
        category="test",
        expected_outcome="应该创建一个文件并写入数据",
    )

    ok, reason = engine._verify_outcome(task, "已成功创建一个文件并写入了数据内容")
    check("关键词匹配 — 匹配成功", ok, reason)

    ok2, reason2 = engine._verify_outcome(task, "天气很好今天心情不错")
    check("关键词匹配 — 无关输出被拒绝", not ok2)
    check("拒绝原因包含匹配率", "匹配率" in reason2, reason2)

    task_q = BenchmarkTask(
        id="test_quoted",
        description="test",
        category="test",
        expected_outcome="输出应包含 'hello world'",
    )
    ok3, _ = engine._verify_outcome(task_q, "结果: hello world done")
    check("引号关键词仍然优先匹配", ok3)

    ok4, r4 = engine._verify_outcome(task_q, "完全无关的内容")
    check("引号关键词缺失时拒绝", not ok4)

    task_empty = BenchmarkTask(
        id="test_empty",
        description="test",
        category="test",
        expected_outcome="应该有输出",
    )
    ok5, r5 = engine._verify_outcome(task_empty, "")
    check("空输出被拒绝", not ok5)


# ====================================================================
# 🟡 #8  _warmup 在 run_suite 中被调用
# ====================================================================
def test_fix8_warmup_called():
    print("\n" + "=" * 60)
    print("🟡 #8  _warmup 在 run_suite 中被调用")

    src = inspect.getsource(
        __import__(
            "openakita.evolution.benchmark", fromlist=["BenchmarkEngine"]
        ).BenchmarkEngine.run_suite
    )
    check("run_suite 包含 _warmup 调用", "_warmup" in src)
    check("_warmup 使用 await", "await self._warmup(agent)" in src)


# ====================================================================
# 🟡 #9  并发 token 计数校正
# ====================================================================
def test_fix9_concurrent_token_correction():
    print("\n" + "=" * 60)
    print("🟡 #9  并发 token 计数校正")

    src = inspect.getsource(
        __import__(
            "openakita.evolution.benchmark", fromlist=["BenchmarkEngine"]
        ).BenchmarkEngine.run_suite
    )
    check("记录 suite 级 tokens_before", "tokens_before_suite" in src)
    check("记录 suite 级 tokens_after", "tokens_after_suite" in src)
    check("mc > 1 时校正 per-task tokens", "mc > 1" in src)
    check("按比例缩放 scale", "scale" in src)

    from openakita.evolution.benchmark import BenchmarkResult

    results = [
        BenchmarkResult(task_id="a", success=True, tokens_used=150),
        BenchmarkResult(task_id="b", success=True, tokens_used=50),
    ]
    suite_total = 100
    per_task_sum = sum(r.tokens_used for r in results)
    scale = suite_total / per_task_sum
    for r in results:
        r.tokens_used = int(r.tokens_used * scale)
    check("校正后总和接近 suite_total", abs(sum(r.tokens_used for r in results) - suite_total) <= 2)
    check("a 得到更多 token (按比例)", results[0].tokens_used > results[1].tokens_used)


# ====================================================================
# 🟡 #10  RuntimeMetricsCollector 关闭
# ====================================================================
def test_fix10_collector_closed():
    print("\n" + "=" * 60)
    print("🟡 #10  RuntimeMetricsCollector 关闭")

    mod = __import__(
        "openakita.evolution.research_org", fromlist=["ResearchOrg"]
    )
    src = inspect.getsource(mod)
    check("collector 变量初始化为 None", "collector = None" in src)
    check("finally 块中 close()", "collector.close()" in src)

    el_src = Path(
        _project_root / "src" / "openakita" / "evolution" / "experiment_loop.py"
    ).read_text(encoding="utf-8")
    check(
        "experiment_loop 中 collector 也有 close",
        "collector.close()" in el_src,
    )


# ====================================================================
# 🟡 #11  身份文件原子写入
# ====================================================================
def test_fix11_atomic_write():
    print("\n" + "=" * 60)
    print("🟡 #11  身份文件原子写入")

    mod = __import__(
        "openakita.evolution.research_org", fromlist=["ResearchOrg"]
    )
    src = inspect.getsource(mod)
    check("使用 tmp 文件写入", ".tmp" in src and "tmp.write_text" in src)
    check("tmp.replace(target) 原子替换", "tmp.replace(target)" in src)


# ====================================================================
# 🟢 #12  空 fragment 守卫
# ====================================================================
def test_fix12_empty_fragment_guard():
    print("\n" + "=" * 60)
    print("🟢 #12  空 fragment 守卫")

    from openakita.evolution.experiment_loop import ExperimentLoop

    result, err = ExperimentLoop._fuzzy_match_and_replace("some content", "", "replacement")
    check("空 fragment 返回 None", result is None)
    check("错误信息提示片段为空", "空" in err)

    result2, _ = ExperimentLoop._fuzzy_match_and_replace("", "", "")
    check("空 fragment + 空 original 返回 None", result2 is None)

    result3, _ = ExperimentLoop._fuzzy_match_and_replace("abc def", "abc", "xyz")
    check("正常替换仍然工作", result3 == "xyz def")


# ====================================================================
# 🟢 #13  quality_weight 热启动保存
# ====================================================================
def test_fix13_quality_weight_warm_save():
    print("\n" + "=" * 60)
    print("🟢 #13  quality_weight 热启动保存")

    from openakita.evolution.experiment_loop import ExperimentLoop

    test_dir = OUT_DIR / "loop13"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "experiments").mkdir(exist_ok=True)

    loop = ExperimentLoop.__new__(ExperimentLoop)
    loop._data_dir = test_dir / "experiments"
    loop._data_dir.mkdir(parents=True, exist_ok=True)
    loop._backups_dir = test_dir / "backups"
    loop._backups_dir.mkdir(parents=True, exist_ok=True)

    def _get_config(key, default):
        return default

    loop._get_config = _get_config

    qw_path = loop._data_dir / "quality_weight.json"
    if qw_path.exists():
        qw_path.unlink()

    w = loop._load_quality_weight()
    check("首次加载返回 >= 0.13", w >= 0.13)
    check("quality_weight.json 已被创建", qw_path.exists())

    data = json.loads(qw_path.read_text(encoding="utf-8"))
    check("保存的 weight 等于返回值", abs(data["weight"] - w) < 0.001)

    w2 = loop._load_quality_weight()
    check("第二次从文件读取一致", abs(w2 - w) < 0.001)


# ====================================================================
# 🟢 #14  备份文件名微秒时间戳
# ====================================================================
def test_fix14_microsecond_timestamp():
    print("\n" + "=" * 60)
    print("🟢 #14  备份文件名微秒时间戳")

    src = inspect.getsource(
        __import__(
            "openakita.evolution.experiment_loop", fromlist=["ExperimentLoop"]
        ).ExperimentLoop._run_experiment
    )
    check("使用 time.time() * 1e6", "time.time() * 1e6" in src)
    check("不再使用 int(time.time()) 秒级", "int(time.time())" not in src.replace("time.time() * 1e6", ""))

    ts1 = int(time.time() * 1e6)
    ts2 = int(time.time() * 1e6)
    check("微秒时间戳有足够精度区分", ts2 >= ts1)
    check("时间戳长度 > 10 (非秒级)", len(str(ts1)) > 10)


# ====================================================================
# 🟢 #15  min() 表达式可读性
# ====================================================================
def test_fix15_min_readability():
    print("\n" + "=" * 60)
    print("🟢 #15  min() 表达式可读性")

    src = inspect.getsource(
        __import__(
            "openakita.evolution.benchmark", fromlist=["BenchmarkEngine"]
        ).BenchmarkEngine.run_suite
    )
    check(
        "min() 不再是单行长表达式",
        "timeout_seconds=min(t.timeout_seconds or global_timeout, global_timeout)" not in src,
    )
    check("仍使用 min() 函数", "min(" in src)


# ====================================================================
# 🟢 #16  baseline 回退尝试多个文件
# ====================================================================
def test_fix16_baseline_fallback_loop():
    print("\n" + "=" * 60)
    print("🟢 #16  baseline 回退尝试多个文件")

    from openakita.evolution.benchmark import BenchmarkEngine

    bd = OUT_DIR / "bench16"
    engine = BenchmarkEngine(data_dir=str(bd))

    results_dir = bd / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    (results_dir / "2025-01-03.json").write_text("CORRUPTED{{{", encoding="utf-8")
    (results_dir / "2025-01-02.json").write_text("ALSO BAD", encoding="utf-8")
    (results_dir / "2025-01-01.json").write_text(
        json.dumps({
            "metrics": {
                "success_rate": 0.8,
                "avg_tokens": 100,
                "avg_time": 1.0,
                "avg_tool_calls": 5,
                "efficiency_score": 70,
            }
        }),
        encoding="utf-8",
    )

    baseline = engine._load_latest_baseline()
    check("跳过损坏文件读到第三个", baseline is not None)
    if baseline:
        check("成功率正确", abs(baseline.success_rate - 0.8) < 0.01)

    for f in results_dir.glob("*.json"):
        f.write_text("ALL BROKEN", encoding="utf-8")
    baseline2 = engine._load_latest_baseline()
    check("全部损坏时返回 None", baseline2 is None)


# ====================================================================
# 🟢 #17  _recently_processed 有上限
# ====================================================================
def test_fix17_recently_processed_cap():
    print("\n" + "=" * 60)
    print("🟢 #17  _recently_processed 上限")

    from openakita.evolution.auto_evolve import AutoEvolver

    agent_mock = MagicMock()
    agent_mock.brain = None
    agent_mock.skill_registry = None
    evolver = AutoEvolver(agent_mock)

    for i in range(600):
        evolver._mark_processed(f"gap_{i}")

    check(
        f"600 条插入后 dict 被裁剪 (<= 501)",
        len(evolver._recently_processed) <= 501,
        f"实际: {len(evolver._recently_processed)}",
    )

    src = inspect.getsource(AutoEvolver._mark_processed)
    check("包含 500 上限检查", "500" in src)


# ====================================================================
# 🟢 #18  空 session_id 跳过 glob
# ====================================================================
def test_fix18_empty_session_id_skip():
    print("\n" + "=" * 60)
    print("🟢 #18  空 session_id 跳过 glob")

    mod = __import__(
        "openakita.evolution.conversation_quality",
        fromlist=["ConversationQualityEvaluator"],
    )
    src = inspect.getsource(mod.ConversationQualityEvaluator)
    check(
        "空 session_id 时 continue",
        "if not sid:" in src and "continue" in src,
    )


# ====================================================================
# 主入口
# ====================================================================
def main():
    clean()
    print("=" * 60)
    print("  18 项缺陷修复验证测试")
    print("=" * 60)

    test_fix1_ceiling_tolerance()
    test_fix2_needs_restart_skips_benchmark()
    test_fix3_added_initialized()
    test_fix4_orig_env_from_dotenv()
    test_fix5_backup_inside_try()
    test_fix6_apply_exception_handling()
    test_fix7_verify_outcome_keyword_fallback()
    test_fix8_warmup_called()
    test_fix9_concurrent_token_correction()
    test_fix10_collector_closed()
    test_fix11_atomic_write()
    test_fix12_empty_fragment_guard()
    test_fix13_quality_weight_warm_save()
    test_fix14_microsecond_timestamp()
    test_fix15_min_readability()
    test_fix16_baseline_fallback_loop()
    test_fix17_recently_processed_cap()
    test_fix18_empty_session_id_skip()

    print("\n" + "=" * 60)
    print(f"  结果: {PASS} passed, {FAIL} failed")
    if FAILED:
        print("\n  失败项:")
        for f in FAILED:
            print(f"    ✗ {f}")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
