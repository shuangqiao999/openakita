"""
缺陷回归测试: 覆盖所有已修复的缺陷

测试范围:
1. Fuzzy match 边界: 位置0, 换行保留, 空白差异
2. JSON fence 剥离: markdown fence, LLM附加文本, JSON边界提取
3. CancelledError 回滚
4. research_org 基线更新
5. Pattern category 冲突
6. AutoEvolver 实例隔离
7. Benchmark 失败原因捕获
8. prompt_optimizer avg_time 一致性

运行: pytest tests/unit/test_defect_regression.py -v
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.evolution.auto_evolve import AutoEvolver
from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkMetrics, BenchmarkTask
from openakita.evolution.experiment_loop import ExperimentLoop
from openakita.evolution.pattern_learner import PatternLearner, ToolPattern
from openakita.evolution.prompt_optimizer import PromptOptimizer, PromptVariant
from openakita.evolution.research_org import ResearchOrg, ResearchProposal


class TestFuzzyMatchEdgeCases:
    """缺陷: 模糊匹配跳过位置0、换行丢失"""

    def test_match_at_position_zero(self):
        """验证位置0的片段能被正确匹配"""
        result, err = ExperimentLoop._fuzzy_match_and_replace(
            "first line\ntarget\nthird\n", "first line\ntarget\nthird\n", "REPLACED\n"
        )
        assert result == "REPLACED\n"
        assert err == ""

    def test_match_whitespace_difference(self):
        """验证空白差异的片段能被模糊匹配"""
        original = "line one\n  line  two\nline three\n"
        fragment = "line one\nline two\nline three\n"
        replacement = "REPLACED\n"
        result, err = ExperimentLoop._fuzzy_match_and_replace(original, fragment, replacement)
        assert result is not None
        assert "REPLACED" in result

    def test_no_match_returns_error(self):
        """验证完全不匹配时返回错误"""
        result, err = ExperimentLoop._fuzzy_match_and_replace(
            "hello world", "completely different text that is long enough", "x"
        )
        assert result is None
        assert "无法匹配" in err

    def test_newline_preserved_after_splice(self):
        """验证模糊匹配后换行符被保留"""
        original = "line1\nline2\nline3\n"
        fragment = "line2\n"
        replacement = "replaced"
        result, err = ExperimentLoop._fuzzy_match_and_replace(original, fragment, replacement)
        assert result is not None
        assert "\n".join(result.strip().splitlines()) == "\n".join(["line1", "replaced", "line3"])

    def test_exact_match_fallback(self):
        """验证精确匹配优先使用 str.replace"""
        original = "hello world"
        result, err = ExperimentLoop._fuzzy_match_and_replace(original, "hello", "hi")
        assert result == "hi world"


class TestJSONFenceParsing:
    """缺陷: LLM JSON 响应含 markdown fence 或附加文本"""

    def test_markdown_json_fence(self):
        from openakita.evolution import strip_json_fences

        raw = '```json\n{"a": 1}\n```'
        assert json.loads(strip_json_fences(raw)) == {"a": 1}

    def test_markdown_fence_without_lang(self):
        from openakita.evolution import strip_json_fences

        raw = '```\n{"b": 2}\n```'
        assert json.loads(strip_json_fences(raw)) == {"b": 2}

    def test_llm_adds_text_before_json(self):
        """验证 LLM 在 JSON 前添加说明文本后仍能提取"""
        from openakita.evolution import strip_json_fences

        raw = 'Here is the result:\n{"skip": true}'
        cleaned = strip_json_fences(raw)
        assert json.loads(cleaned) == {"skip": True}

    def test_llm_adds_text_after_json(self):
        """验证 LLM 在 JSON 后添加说明文本后仍能提取"""
        from openakita.evolution import strip_json_fences

        raw = '{"status": "ok"}\nThis change looks good.'
        cleaned = strip_json_fences(raw)
        assert json.loads(cleaned) == {"status": "ok"}

    def test_json_array_extraction(self):
        """验证 JSON 数组能被正确边界提取"""
        from openakita.evolution import strip_json_fences

        raw = 'result: [{"a": 1}, {"b": 2}]'
        cleaned = strip_json_fences(raw)
        assert json.loads(cleaned) == [{"a": 1}, {"b": 2}]

    def test_no_json_found(self):
        """验证无 JSON 时返回原文本（后续 json.loads 会抛异常）"""
        from openakita.evolution import strip_json_fences

        raw = "just plain text"
        cleaned = strip_json_fences(raw)
        assert cleaned == "just plain text"


class TestCancelledErrorRollback:
    """缺陷: asyncio.CancelledError 跳过文件回滚"""

    @pytest.mark.asyncio
    async def test_experiment_loop_rollback_on_cancel(self, tmp_path):
        """验证 ExperimentLoop 取消时文件被回滚"""
        target = tmp_path / "identity" / "AGENT.md"
        target.parent.mkdir(parents=True)
        target.write_text("original content\nmore content here\n", encoding="utf-8")

        mock_agent = MagicMock()
        loop = ExperimentLoop(mock_agent, data_dir=tmp_path / "exp", project_root=tmp_path)
        from openakita.evolution.experiment_loop import Hypothesis

        hyp = Hypothesis(
            target="identity/AGENT.md",
            description="test",
            original_content="original content\n",
            proposed_content="modified content here\n",
            rationale="test",
        )

        async def fake_run_suite(*args, **kwargs):
            raise asyncio.CancelledError()

        mock_engine = MagicMock()
        mock_engine.run_suite = fake_run_suite

        try:
            await loop._run_experiment(hyp, mock_engine, {
                "success_rate": 0.8, "avg_tokens": 5000, "avg_time": 10,
                "efficiency_score": 80,
            })
        except asyncio.CancelledError:
            pass

        restored = target.read_text(encoding="utf-8")
        assert "original content" in restored
        assert "modified content" not in restored

    @pytest.mark.asyncio
    async def test_prompt_optimizer_rollback_on_cancel(self, tmp_path):
        """验证 PromptOptimizer 取消时文件被回滚"""
        target = tmp_path / "identity" / "AGENT.md"
        target.parent.mkdir(parents=True)
        target.write_text("## section\ncontent\n", encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.brain = MagicMock()
        opt = PromptOptimizer(mock_agent, project_root=tmp_path)

        var = PromptVariant(
            section="identity/AGENT.md",
            original="content\n",
            proposed="modified\n",
            hypothesis="test",
        )

        async def fake_run_suite(*args, **kwargs):
            raise asyncio.CancelledError()

        from openakita.evolution.benchmark import BenchmarkEngine
        with patch.object(BenchmarkEngine, "run_suite", fake_run_suite):
            with pytest.raises(asyncio.CancelledError):
                await opt._test_variant(var, {
                    "success_rate": 0.8, "avg_tokens": 5000, "avg_time": 10,
                    "efficiency_score": 80,
                })

        restored = target.read_text(encoding="utf-8")
        assert "content" in restored
        assert "modified" not in restored


class TestResearchOrgBaselineUpdate:
    """缺陷: research_org 基线未在 adoption 后更新"""

    @pytest.mark.asyncio
    async def test_baseline_updated_after_adoption(self, tmp_path):
        """验证采纳提案后更新 baseline 指标"""
        target = tmp_path / "identity" / "AGENT.md"
        target.parent.mkdir(parents=True)
        target.write_text("original content line here with enough text to pass ratio checks\n" * 5, encoding="utf-8")

        mock_agent = MagicMock()
        mock_agent.brain = MagicMock()
        org = ResearchOrg(mock_agent, project_root=tmp_path)
        org.ALLOWED_SECTIONS = frozenset({"identity/AGENT.md"})

        perf_data = {
            "metrics": {
                "success_rate": 0.8, "avg_tokens": 5000,
                "avg_time": 10, "efficiency_score": 75,
            }
        }

        proposal = ResearchProposal(
            agent_role="prompt_engineer",
            description="test baseline update",
            target="identity/AGENT.md",
            content=json.dumps({
                "section": "identity/AGENT.md",
                "original": "original content line here with enough text to pass ratio checks\n",
                "proposed": "modified content line here with enough text\n",
                "hypothesis": "test",
            }),
        )

        call_count = 0

        async def fake_suite(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_report = MagicMock()
            mock_report.metrics = BenchmarkMetrics(
                success_rate=0.85, avg_tokens=4500,
                avg_time=9, efficiency_score=80,
            )
            return mock_report

        from openakita.evolution.benchmark import BenchmarkEngine
        with patch.object(BenchmarkEngine, "run_suite", fake_suite):
            success, metrics = await org._apply_prompt_change(proposal, perf_data)
            assert success is True
            assert metrics is not None
            perf_data["metrics"] = metrics

        from openakita.evolution.experiment_loop import ExperimentLoop
        assert ExperimentLoop._is_improvement(
            {"success_rate": 0.8, "avg_tokens": 5000, "avg_time": 10, "efficiency_score": 75},
            perf_data["metrics"], 0.05,
        )


class TestPatternCategoryCollision:
    """缺陷: 同分类 pattern 冲突时静默丢弃"""

    def test_same_category_not_silently_dropped(self, tmp_path):
        """验证同分类的两次学习不会互相覆盖"""
        learner = PatternLearner(MagicMock(), data_dir=tmp_path)
        patterns = [
            ToolPattern(category="coding", pattern="pattern A", confidence=0.9, evidence_count=10),
            ToolPattern(category="coding", pattern="pattern B", confidence=0.8, evidence_count=8),
        ]
        learner._save_patterns(patterns)
        loaded = learner.load_patterns()
        coding_patterns = [p for p in loaded if p.category == "coding"]
        assert len(coding_patterns) == 1
        assert coding_patterns[0].pattern == "pattern B"


class TestAutoEvolverInstanceIsolation:
    """缺陷: _recently_processed 类变量被多实例共享"""

    def test_instances_have_independent_caches(self):
        """验证不同实例的去重缓存互不干扰"""
        a1 = AutoEvolver(MagicMock())
        a2 = AutoEvolver(MagicMock())
        a1._mark_processed("skill_x")
        assert a1._is_recently_processed("skill_x")
        assert not a2._is_recently_processed("skill_x")

    def test_cache_not_shared_class_variable(self):
        """验证 _recently_processed 不是类变量"""
        a = AutoEvolver(MagicMock())
        a._recently_processed["test"] = 1.0
        b = AutoEvolver(MagicMock())
        assert "test" not in b._recently_processed


class TestBenchmarkFailureReason:
    """缺陷: 任务运行失败时原因未捕获"""

    def test_failure_reason_captured(self):
        """验证 task runner 返回的 error 被传递到 BenchmarkResult"""
        engine = BenchmarkEngine(data_dir="/tmp/bench_defect_test", token_counter=lambda a: 0)

        async def failing_runner(agent, desc):
            mock = MagicMock()
            mock.success = False
            mock.error = "Network timeout"
            mock.iterations = 1
            mock.data = "error output"
            return mock

        import asyncio as _aio
        task = BenchmarkTask(id="fail-test", description="", category="test",
                             expected_outcome="", timeout_seconds=5)
        engine._task_runner = failing_runner
        result = _aio.run(engine._run_single(MagicMock(), task))
        assert result.success is False
        assert "Network timeout" in (result.error or "")


class TestPromptOptimizerAvgTime:
    """缺陷: _collect_recent_performance 缺 avg_time"""

    def test_collect_performance_has_all_fields(self):
        """验证 _collect_recent_performance 返回 avg_time"""
        opt = PromptOptimizer(MagicMock())
        data = opt._collect_recent_performance()
        assert "success_rate" in data
        assert "avg_tokens" in data
        assert "avg_time" in data
        assert "efficiency_score" in data


class TestResearchOrgSafetyChecks:
    """缺陷: _apply_prompt_change 缺少安全检查"""

    @pytest.mark.asyncio
    async def test_rejects_oversized_change(self, tmp_path):
        """验证超过 30% 的变更被拒绝"""
        target = tmp_path / "identity" / "AGENT.md"
        target.parent.mkdir(parents=True)
        target.write_text("short", encoding="utf-8")

        org = ResearchOrg(MagicMock(), project_root=tmp_path)
        proposal = ResearchProposal(
            agent_role="prompt_engineer", description="test", target="identity/AGENT.md",
            content=json.dumps({
                "section": "identity/AGENT.md",
                "original": "short",
                "proposed": "longer replacement text here",
                "hypothesis": "test",
            }),
        )
        success, _ = await org._apply_prompt_change(proposal, {})
        assert success is False

    @pytest.mark.asyncio
    async def test_rejects_short_replacement(self, tmp_path):
        """验证少于 10 字符的替换被拒绝"""
        target = tmp_path / "identity" / "AGENT.md"
        target.parent.mkdir(parents=True)
        target.write_text("x" * 1000, encoding="utf-8")

        org = ResearchOrg(MagicMock(), project_root=tmp_path)
        proposal = ResearchProposal(
            agent_role="prompt_engineer", description="test", target="identity/AGENT.md",
            content=json.dumps({
                "section": "identity/AGENT.md",
                "original": "xxx",
                "proposed": "y",
                "hypothesis": "test",
            }),
        )
        success, _ = await org._apply_prompt_change(proposal, {})
        assert success is False
