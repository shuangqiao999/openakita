"""
数据驱动自进化 补充功能测试

测试:
1. load_weekly_average min_samples → None
2. _extract_total_tokens 多种格式
3. _is_task_valid 验证器
4. RuntimeSnapshot 新字段
5. 记忆调优冷却 get/record_tuning_time
"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

# ================================================================
print("=== 1. load_weekly_average min_samples ===")
from openakita.evolution.conversation_quality import ConversationQualityEvaluator

ev = ConversationQualityEvaluator(MagicMock(), data_dir="/tmp/cq_test")
r = ev.load_weekly_average(min_samples=10)
assert r is None, f"Expected None with 0 samples, got {r}"
print("  [PASS] 样本不足返回 None")

# ================================================================
print("\n=== 2. _extract_total_tokens ===")
from openakita.evolution.runtime_metrics import RuntimeMetricsCollector

# Format A: top-level total_tokens dict
d1 = {"total_tokens": {"input": 100, "output": 50}}
assert RuntimeMetricsCollector._extract_total_tokens(d1) == 150
print("  [PASS] top-level total_tokens dict = 150")

# Format B: top-level total_tokens int
d2 = {"total_tokens": 200}
assert RuntimeMetricsCollector._extract_total_tokens(d2) == 200
print("  [PASS] top-level total_tokens int = 200")

# Format C: iterations tokens + tokens_used
d3 = {"iterations": [
    {"tokens_used": 30, "tokens": {"input": 10, "output": 5}},
    {"tokens_used": 40, "tokens": {"input": 20, "output": 10}},
]}
assert RuntimeMetricsCollector._extract_total_tokens(d3) == 115
print("  [PASS] iterations sum = 115")

# Format D: empty trace
d4 = {}
assert RuntimeMetricsCollector._extract_total_tokens(d4) == 0
print("  [PASS] empty trace = 0")

# ================================================================
print("\n=== 3. RuntimeSnapshot 新字段 ===")
from openakita.evolution.runtime_metrics import RuntimeSnapshot
snap = RuntimeSnapshot()
assert snap.conversation_success_rate == 0.0
assert snap.conversation_avg_tokens == 0.0
assert snap.memory_usage_rate == 0.0
print("  [PASS] 3 new fields exist with default 0.0")

# ================================================================
print("\n=== 4. _is_task_valid 验证器 ===")
from openakita.evolution.dynamic_benchmark import DynamicBenchmarkGenerator

# Valid task
ok = DynamicBenchmarkGenerator._is_task_valid({
    "description": "编写 Python 函数计算斐波那契数",
    "expected_outcome": "fib(10)=55，代码执行成功",
    "timeout_seconds": 300,
    "category": "coding",
})
assert ok, "Valid task should pass"
print("  [PASS] valid task passes")

# No action verb
bad1 = DynamicBenchmarkGenerator._is_task_valid({
    "description": "斐波那契数列",
    "expected_outcome": "计算结果为55",
    "timeout_seconds": 300,
    "category": "coding",
})
assert not bad1, "No verb should fail"
print("  [PASS] no verb rejected")

# Short expected
bad2 = DynamicBenchmarkGenerator._is_task_valid({
    "description": "编写代码",
    "expected_outcome": "ok",
    "timeout_seconds": 300,
    "category": "coding",
})
assert not bad2, "Short expected should fail"
print("  [PASS] short expected rejected")

# Bad timeout
bad3 = DynamicBenchmarkGenerator._is_task_valid({
    "description": "编写代码",
    "expected_outcome": "代码执行成功且结果正确",
    "timeout_seconds": 10,
    "category": "coding",
})
assert not bad3, "Timeout 10 should fail"
print("  [PASS] bad timeout rejected")

# coding without code words
bad4 = DynamicBenchmarkGenerator._is_task_valid({
    "description": "编写函数",
    "expected_outcome": "函数正常运行",
    "timeout_seconds": 300,
    "category": "coding",
})
assert not bad4, "Coding without code words should fail"
print("  [PASS] coding without code words rejected")

# ================================================================
print("\n=== 5. 记忆调优冷却 ===")
import shutil
_cd = Path("/tmp/rm_cooldown_test")
if _cd.exists():
    shutil.rmtree(str(_cd))
_cd.mkdir(parents=True)
collector = RuntimeMetricsCollector(data_dir="/tmp/rm_cooldown_test")
assert collector.get_last_tuning_time() == 0.0
print("  [PASS] get_last_tuning_time default = 0")

collector.record_tuning_time()
ts = collector.get_last_tuning_time()
assert ts > 0
print("  [PASS] record_tuning_time works")

# ================================================================
print("\n=== 6. Config 新字段 ===")
from openakita.config import settings
assert settings.quality_min_weekly_samples == 10
assert settings.memory_tuning_cooldown_hours == 24
assert settings.memory_usage_low_threshold == 0.3
assert settings.memory_retrieval_tuning_enabled is True
assert settings.benchmark_generate_from_traces is False
print("  [PASS] 5 new config fields verified")

# ================================================================
print("\n=== 7. quality_weight_in_improvement 默认值 ===")
assert settings.quality_weight_in_improvement == 0.10, \
    f"Expected 0.10, got {settings.quality_weight_in_improvement}"
print("  [PASS] quality_weight_in_improvement = 0.10 (保守起点)")

print("\n" + "=" * 50)
print("ALL TESTS PASSED")
