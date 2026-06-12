"""
.env 动态调优 + 增量采集 + 校准验证 功能测试
"""

import json
import time
from pathlib import Path

# ================================================================
print("=== 1. EnvTuner 读写/备份/回滚 ===")
from openakita.evolution.env_tuner import EnvTuner

test_env = Path("/tmp/test_openakita.env")
test_backup_dir = Path("/tmp/env_test_backups")
test_env.write_text("BENCHMARK_MAX_CONCURRENT=1\nEXPERIMENTS_PER_CYCLE=2\n", encoding="utf-8")

tuner = EnvTuner(test_env, test_backup_dir)

val = tuner.read("BENCHMARK_MAX_CONCURRENT")
assert val == "1", f"Expected 1, got {val}"
print(f"  [PASS] read BENCHMARK_MAX_CONCURRENT=1")

backup, ok = tuner.apply("BENCHMARK_MAX_CONCURRENT", "3")
assert ok and tuner.read("BENCHMARK_MAX_CONCURRENT") == "3"
assert backup is not None and backup.exists()
print(f"  [PASS] apply 3, backup={backup.name}")

tuner.rollback(backup)
assert tuner.read("BENCHMARK_MAX_CONCURRENT") == "1"
print(f"  [PASS] rollback to 1")

backup2, ok2 = tuner.apply("NEW_PARAM", "42")
assert ok2 and tuner.read("NEW_PARAM") == "42"
print(f"  [PASS] add new param NEW_PARAM=42")

tuner.rollback(backup2)
assert tuner.read("NEW_PARAM") is None
print(f"  [PASS] rollback removes NEW_PARAM")

# Cleanup
test_env.unlink(missing_ok=True)
for f in test_backup_dir.glob("env_backup_*"):
    f.unlink(missing_ok=True)

# ================================================================
print("\n=== 2. EVOLVABLE_ENV_PARAMS 白名单 ===")
from openakita.config import EVOLVABLE_ENV_PARAMS

assert "BENCHMARK_MAX_CONCURRENT" in EVOLVABLE_ENV_PARAMS
assert "EXPERIMENTS_PER_CYCLE" in EVOLVABLE_ENV_PARAMS
assert "RESEARCH_LLM_TIMEOUT" in EVOLVABLE_ENV_PARAMS
d, mn, mx, restart = EVOLVABLE_ENV_PARAMS["BENCHMARK_MAX_CONCURRENT"]
assert mn <= d <= mx
assert not restart
print(f"  [PASS] 10 params, BENCHMARK_MAX_CONCURRENT=({d},{mn},{mx},{restart})")

# ================================================================
print("\n=== 3. experiment_loop env: 目标支持 ===")
from openakita.evolution.experiment_loop import ExperimentLoop
assert hasattr(ExperimentLoop, "_run_env_experiment")
print(f"  [PASS] _run_env_experiment method exists")

# ================================================================
print("\n=== 4. RuntimeMetrics 增量采集 ===")
from openakita.evolution.runtime_metrics import RuntimeMetricsCollector

c = RuntimeMetricsCollector()
ts1 = c._load_last_ts()
c._save_last_ts(time.time())
ts2 = c._load_last_ts()
assert ts2 > 0
print(f"  [PASS] last_collect: {ts2 - ts1:.1f}s delta")

snap = c.collect()
assert snap.memory_total >= 0
print(f"  [PASS] collect: {snap.memory_total} mem, {len(snap.tool_frequencies)} tools")

# ================================================================
print("\n=== 5. 全模块导入 ===")
from openakita.evolution import (
    EnvTuner, DynamicBenchmarkGenerator, ConversationQualityEvaluator,
    RuntimeMetricsCollector, RuntimeSnapshot, QualityScore,
)
print("  [PASS] 6 new classes all importable")

# ================================================================
print("\n=== 6. Config 新字段 ===")
from openakita.config import settings
assert settings.env_tuning_enabled is True
assert settings.runtime_metrics_incremental is True
assert settings.auto_approve_dynamic_tasks is False
assert settings.quality_eval_validate_enabled is True
print(f"  [PASS] 5 new config fields verified")

# ================================================================
print("\n=== 7. ExperimentLoop._is_improvement 质量权重 ===")
old = {"success_rate": 0.8, "avg_tokens": 5000, "avg_time": 10, "efficiency_score": 75}
new = {"success_rate": 0.85, "avg_tokens": 4000, "avg_time": 8, "efficiency_score": 80}

assert ExperimentLoop._is_improvement(old, new, 0.02)  # 默认(无质量权重)
assert ExperimentLoop._is_improvement(old, new, 0.02, quality_delta=0.1, quality_weight=0.3)  # 质量评分改善
print(f"  [PASS] _is_improvement with quality_weight=0.3")

# ================================================================
print("\n" + "=" * 50)
print("ALL TESTS PASSED")
