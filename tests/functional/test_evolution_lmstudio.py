"""
功能测试: 自进化系统 — LMStudio 本地 API 集成测试

前置条件:
- LMStudio 运行在 http://localhost:1234/v1
- 模型已加载（推荐 Qwen2.5 或 DeepSeek）

运行:
    pytest tests/functional/test_evolution_lmstudio.py -v --tb=short

或跳过网络测试:
    pytest tests/functional/test_evolution_lmstudio.py -v -k "not http"
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

logger = logging.getLogger(__name__)

LMSTUDIO_BASE = "http://localhost:1234/v1"
LMSTUDIO_TIMEOUT = 30


# ── LMStudio API 客户端 ──

def _http_post(url: str, payload: dict, timeout: int = LMSTUDIO_TIMEOUT) -> dict:
    import urllib.error
    import urllib.request

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": str(e)}


def lmstudio_chat_simple(prompt: str, max_tokens: int = 1024) -> str:
    payload = {
        "model": "auto",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
    }
    result = _http_post(f"{LMSTUDIO_BASE}/chat/completions", payload)
    if "error" in result:
        return f"[ERROR] {result['error']}"
    return result["choices"][0]["message"]["content"]


def lmstudio_available() -> bool:
    try:
        result = _http_post(f"{LMSTUDIO_BASE}/models", {}, timeout=5)
        return "error" not in result
    except Exception:
        return False


# ── 测试 ──_

@pytest.mark.skipif(not lmstudio_available(), reason="LMStudio 不可用")
class TestLMStudioConnectivity:
    def test_lmstudio_reachable(self):
        """验证 LMStudio 可访问"""
        assert lmstudio_available(), "LMStudio 未启动"

    def test_chat_simple_returns_text(self):
        """验证 chat_simple 返回有效文本"""
        response = lmstudio_chat_simple("说一个字: 好", max_tokens=10)
        assert response and len(response) > 0
        assert "ERROR" not in response


class TestJSONFenceParsing:
    def test_strip_json_fences_from_init(self):
        """验证 strip_json_fences 去除 markdown 代码栏"""
        from openakita.evolution import strip_json_fences

        raw = '```json\n{"a": 1}\n```'
        assert strip_json_fences(raw) == '{"a": 1}'

        raw2 = '```\n{"b": 2}\n```'
        assert strip_json_fences(raw2) == '{"b": 2}'

    def test_strip_no_fences(self):
        """无代码栏时原样返回"""
        from openakita.evolution import strip_json_fences

        raw = '{"a": 1}'
        assert strip_json_fences(raw) == '{"a": 1}'


class TestBenchmarkEngineUnit:
    """单元测试（无需 LLM）"""

    def test_benchmark_engine_create(self, tmp_path):
        from openakita.evolution.benchmark import BenchmarkEngine

        engine = BenchmarkEngine(data_dir=tmp_path / "bench")
        tasks = engine.load_tasks()
        assert len(tasks) == 8

    def test_benchmark_task_verification(self):
        from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkTask

        engine = BenchmarkEngine()
        task = BenchmarkTask(
            id="test", description="", category="test",
            expected_outcome="输出 '4950' 结果正确",
        )
        ok, _ = engine._verify_outcome(task, "计算结果为 4950，验证通过")
        assert ok is True
        fail, reason = engine._verify_outcome(task, "结果不对")
        assert fail is False
        assert "4950" in reason


class TestExperimentLoopUnit:
    def test_fuzzy_match_and_replace(self):
        from openakita.evolution.experiment_loop import ExperimentLoop

        result, err = ExperimentLoop._fuzzy_match_and_replace(
            "hello world\nline two\n", "hello world\nline two\n", "REPLACED\n"
        )
        assert result == "REPLACED\n"
        assert err == ""

    def test_is_improvement_sr_hard_constraint(self):
        from openakita.evolution.experiment_loop import ExperimentLoop

        old = {"success_rate": 0.8, "avg_tokens": 5000, "avg_time": 10}
        new_worse = {"success_rate": 0.6, "avg_tokens": 1000, "avg_time": 3}
        assert not ExperimentLoop._is_improvement(old, new_worse, 0.02)

    def test_is_improvement_accepted(self):
        from openakita.evolution.experiment_loop import ExperimentLoop

        old = {"success_rate": 0.7, "avg_tokens": 5000, "avg_time": 10}
        new_better = {"success_rate": 0.85, "avg_tokens": 3000, "avg_time": 6}
        assert ExperimentLoop._is_improvement(old, new_better, 0.02)

    def test_syntax_validation_python(self):
        from openakita.evolution.experiment_loop import ExperimentLoop

        ok, _ = ExperimentLoop._validate_syntax(Path("test.py"), "print('hello')\n")
        assert ok is True
        ok, reason = ExperimentLoop._validate_syntax(Path("test.py"), "def foo(\n")
        assert ok is False

    def test_syntax_validation_yaml(self):
        from openakita.evolution.experiment_loop import ExperimentLoop

        ok, _ = ExperimentLoop._validate_syntax(Path("test.yaml"), "key: value\n")
        assert ok is True


class TestPatternLearnerUnit:
    def test_extract_tool_names_nested(self):
        from openakita.evolution.pattern_learner import PatternLearner

        data = {
            "iterations": [
                {"tool_name": "read_file"},
                {"tool_calls": [
                    {"name": "grep", "tool_input": {}, "tool_call_id": "1"}
                ]},
            ]
        }
        tools = PatternLearner._extract_tool_names(data["iterations"])
        assert "read_file" in tools
        assert "grep" in tools

    def test_jaccard_dedup(self, tmp_path):
        from openakita.evolution.pattern_learner import PatternLearner, ToolPattern

        learner = PatternLearner(MagicMock(), data_dir=tmp_path)
        patterns = [
            ToolPattern(category="a", pattern="grep read_file edit_file check", confidence=0.9, evidence_count=10),
            ToolPattern(category="b", pattern="grep edit_file read_file check lints", confidence=0.7, evidence_count=5),
        ]
        result = learner._deduplicate_patterns(patterns)
        assert len(result) == 1


class TestAutoEvolverUnit:
    def test_dedup_cache(self):
        from openakita.evolution.auto_evolve import AutoEvolver

        AutoEvolver._recently_processed.clear()
        evolver = AutoEvolver(MagicMock())
        assert not evolver._is_recently_processed("test_cap")
        evolver._mark_processed("test_cap")
        assert evolver._is_recently_processed("test_cap")

    def test_dependency_injection(self):
        from openakita.evolution.auto_evolve import AutoEvolver

        mock_installer = MagicMock()
        mock_skill_gen = MagicMock()
        mock_analyzer = MagicMock()
        evolver = AutoEvolver(
            MagicMock(), installer=mock_installer, skill_gen=mock_skill_gen, need_analyzer=mock_analyzer,
        )
        assert evolver._get_installer() is mock_installer
        assert evolver._get_analyzer() is mock_analyzer


@pytest.mark.skipif(not lmstudio_available(), reason="LMStudio 不可用")
class TestLMStudioLLMIntegration:
    """通过 LMStudio 测试 LLM 相关功能"""

    def test_json_strip_with_llm_output(self):
        """验证 LMStudio 输出带 fence 的 JSON 能被正确解析"""
        prompt = '输出JSON: {"status": "ok", "value": 42}'
        response = lmstudio_chat_simple(prompt, max_tokens=100)
        from openakita.evolution import strip_json_fences

        cleaned = strip_json_fences(response)
        try:
            data = json.loads(cleaned)
            assert isinstance(data, dict)
        except json.JSONDecodeError:
            pytest.skip("LMStudio 未返回有效 JSON")

    def test_llm_summarize_pattern(self):
        """验证 LLM 能总结工具调用模式"""
        prompt = (
            '工具序列: read_file → grep → edit_file → read_file\n'
            '总结为一句话 best practice（不要加引号）: 在修改代码文件时，应该'
        )
        response = lmstudio_chat_simple(prompt, max_tokens=50)
        assert len(response) > 5
        assert "ERROR" not in response

    def test_llm_propose_analysis(self):
        """验证 LLM 能分析性能数据"""
        prompt = (
            '性能: 成功率 80%, 平均 token 5000\n'
            '失败: web_search 超时\n'
            '输出JSON: [{"opportunity": "描述", "priority": 8, "category": "tool"}]\n'
            '只输出 JSON 数组，不要解释。'
        )
        response = lmstudio_chat_simple(prompt, max_tokens=200)
        from openakita.evolution import strip_json_fences

        cleaned = strip_json_fences(response)
        try:
            data = json.loads(cleaned)
            assert isinstance(data, list)
        except json.JSONDecodeError:
            pytest.skip("LMStudio 未返回有效 JSON 数组")

    def test_llm_audit_proposal(self):
        """验证 LLM 能审计提案"""
        prompt = (
            '修改方案: 修改 identity/AGENT.md 中的工具使用指南\n'
            '请检查安全性。输出 JSON: {"approved": true, "reason": "安全", "risk_level": "low"}'
        )
        response = lmstudio_chat_simple(prompt, max_tokens=100)
        from openakita.evolution import strip_json_fences

        cleaned = strip_json_fences(response)
        try:
            data = json.loads(cleaned)
            assert "approved" in data
        except json.JSONDecodeError:
            pytest.skip("LMStudio 未返回有效 JSON")

    def test_llm_generate_hypothesis(self):
        """验证 LLM 能生成实验假设"""
        prompt = (
            'Agent 成功率 80%，token 消耗偏高。\n'
            '可修改 prompt 减少 token。\n'
            '输出 JSON: {"target": "identity/AGENT.md", "description": "...", '
            '"rationale": "...", "proposed_change": "...", "original_fragment": "..."}'
        )
        response = lmstudio_chat_simple(prompt, max_tokens=300)
        assert len(response) > 10
        assert "ERROR" not in response


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
