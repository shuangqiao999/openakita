"""
功能测试: 自进化系统 (P0-P4)

覆盖:
1. AutoEvolver 基本逻辑
2. BenchmarkEngine 任务加载和指标计算
3. ExperimentLoop 安全护栏 (路径校验/内容校验/回滚)
4. PromptOptimizer 路径白名单校验
5. PatternLearner 数据提取和模式输出
6. ResearchOrg 安全检查
7. 执行器 agent 注入和 guard
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.evolution.auto_evolve import AutoEvolver, EvolutionResult
from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkMetrics, BenchmarkTask
from openakita.evolution.experiment_loop import ExperimentLoop, ExperimentResult, Hypothesis
from openakita.evolution.pattern_learner import PatternLearner, ToolPattern
from openakita.evolution.prompt_optimizer import PromptOptimizer, PromptVariant
from openakita.evolution.research_org import ResearchOrg


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.brain = MagicMock()
    agent.brain.chat_simple = AsyncMock(return_value='{"skip": true}')
    agent.brain.total_tokens_used = 0
    agent.skill_registry = MagicMock()
    agent.skill_generator = MagicMock()
    agent.execute_task_from_message = AsyncMock(
        return_value=MagicMock(success=True, data="ok", iterations=3, duration_seconds=5.0)
    )
    return agent


class TestAutoEvolver:
    @pytest.mark.asyncio
    async def test_skip_non_evolvable_gap(self):
        agent = MagicMock()
        evolver = AutoEvolver(agent)
        result = await evolver.respond_to_failure(
            task_description="test", harness_gap="loop_detected"
        )
        assert result.action == "skip"
        assert "非可进化" in result.reason

    @pytest.mark.asyncio
    async def test_skip_without_brain(self):
        agent = MagicMock()
        agent.brain = None
        evolver = AutoEvolver(agent)
        result = await evolver.respond_to_failure(
            task_description="test", harness_gap="missing_tool"
        )
        assert result.action == "skip"


class TestBenchmarkEngine:
    def test_load_default_tasks(self):
        engine = BenchmarkEngine()
        tasks = engine.load_tasks()
        assert len(tasks) == 8
        assert all(isinstance(t, BenchmarkTask) for t in tasks)

    def test_all_tasks_have_required_fields(self):
        engine = BenchmarkEngine()
        tasks = engine.load_tasks()
        for t in tasks:
            assert t.id
            assert t.description
            assert t.category
            assert t.expected_outcome
            assert t.timeout_seconds > 0

    def test_compute_metrics_empty(self):
        engine = BenchmarkEngine()
        metrics = engine._compute_metrics([], [])
        assert metrics.success_rate == 0.0

    def test_compute_metrics_with_results(self):
        from openakita.evolution.benchmark import BenchmarkResult

        engine = BenchmarkEngine()
        tasks = [BenchmarkTask(id="t1", description="", category="test", expected_outcome="")]
        results = [BenchmarkResult(task_id="t1", success=True, tokens_used=1000, time_seconds=5.0)]
        metrics = engine._compute_metrics(results, tasks)
        assert metrics.success_rate == 1.0
        assert metrics.avg_tokens == 1000.0

    def test_save_and_load_baseline(self, tmp_path):
        engine = BenchmarkEngine(data_dir=tmp_path / "bench")
        from openakita.evolution.benchmark import BenchmarkReport

        report = BenchmarkReport(
            timestamp="2026-01-01",
            metrics=BenchmarkMetrics(success_rate=0.8, avg_tokens=5000, efficiency_score=75.0),
        )
        engine.save_as_baseline(report)
        loaded = engine._load_latest_baseline()
        assert loaded is not None
        assert loaded.success_rate == 0.8


class TestExperimentLoopSafety:
    @pytest.mark.asyncio
    async def test_rejects_target_not_in_whitelist(self, mock_agent):
        loop = ExperimentLoop(mock_agent)
        hypothesis = Hypothesis(
            target="src/openakita/core/brain.py",
            description="test",
            original_content="old",
            proposed_content="new content here",
            rationale="test",
        )
        result = await loop._run_experiment(hypothesis, MagicMock(), {})
        assert result.action == "error"
        assert "允许列表" in result.reason

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self, mock_agent, tmp_path):
        loop = ExperimentLoop(mock_agent)
        hypothesis = Hypothesis(
            target="../../../etc/passwd",
            description="test",
            original_content="old",
            proposed_content="new content here",
            rationale="test",
        )
        loop.MUTABLE_TARGETS = ["../../../etc/passwd"]
        result = await loop._run_experiment(hypothesis, MagicMock(), {})
        assert result.action == "error"
        assert "路径" in result.reason or "不存在" in result.reason

    @pytest.mark.asyncio
    async def test_rejects_oversized_change(self, mock_agent, tmp_path):
        with patch("openakita.config.settings") as mock_settings:
            mock_settings.project_root = tmp_path
            target_file = tmp_path / "identity" / "AGENT.md"
            target_file.parent.mkdir(parents=True)
            target_file.write_text("short content", encoding="utf-8")

            loop = ExperimentLoop(mock_agent, data_dir=tmp_path / "exp")
            hypothesis = Hypothesis(
                target="identity/AGENT.md",
                description="test",
                original_content="short content",
                proposed_content="replacement",
                rationale="test",
            )
            result = await loop._run_experiment(hypothesis, MagicMock(), {})
            assert result.action == "error"
            assert "30%" in result.reason

    @pytest.mark.asyncio
    async def test_rejects_short_replacement(self, mock_agent, tmp_path):
        with patch("openakita.config.settings") as mock_settings:
            mock_settings.project_root = tmp_path
            target_file = tmp_path / "identity" / "AGENT.md"
            target_file.parent.mkdir(parents=True)
            target_file.write_text("x" * 1000, encoding="utf-8")

            loop = ExperimentLoop(mock_agent, data_dir=tmp_path / "exp")
            hypothesis = Hypothesis(
                target="identity/AGENT.md",
                description="test",
                original_content="xxx",
                proposed_content="y",
                rationale="test",
            )
            result = await loop._run_experiment(hypothesis, MagicMock(), {})
            assert result.action == "error"
            assert "过短" in result.reason


class TestPromptOptimizerSafety:
    @pytest.mark.asyncio
    async def test_rejects_non_whitelisted_section(self, mock_agent):
        optimizer = PromptOptimizer(mock_agent)
        variant = PromptVariant(
            section="src/openakita/core/brain.py",
            original="old",
            proposed="new content here",
            hypothesis="test",
        )
        result = await optimizer._test_variant(variant, {"efficiency_score": 50})
        assert result.reason == "目标不在允许列表中"

    def test_validate_change_ratio_reject_large(self, mock_agent, tmp_path):
        with patch("openakita.config.settings") as mock_settings:
            mock_settings.project_root = tmp_path
            target = tmp_path / "identity" / "AGENT.md"
            target.parent.mkdir(parents=True)
            target.write_text("x" * 100, encoding="utf-8")

            optimizer = PromptOptimizer(mock_agent)
            variant = PromptVariant(
                section="identity/AGENT.md",
                original="x" * 50,
                proposed="y" * 50,
                hypothesis="test",
            )
            assert optimizer._validate_change_ratio(variant) is False


class TestResearchOrgSafety:
    @pytest.mark.asyncio
    async def test_rejects_non_allowed_section(self, mock_agent):
        from openakita.evolution.research_org import ResearchProposal

        org = ResearchOrg(mock_agent)
        proposal = ResearchProposal(
            agent_role="prompt_engineer",
            description="test",
            target="src/openakita/core/brain.py",
            content=json.dumps({"section": "src/openakita/core/brain.py", "original": "a", "proposed": "b"}),
        )
        result = await org._apply_prompt_change(proposal, {})
        assert result is False


class TestPatternLearner:
    def test_load_empty_patterns(self, tmp_path):
        learner = PatternLearner(MagicMock(), data_dir=tmp_path)
        patterns = learner.load_patterns()
        assert patterns == []

    def test_save_and_load_patterns(self, tmp_path):
        learner = PatternLearner(MagicMock(), data_dir=tmp_path)
        patterns = [
            ToolPattern(
                category="coding",
                pattern="先 grep 定位再 edit 修改",
                confidence=0.9,
                evidence_count=15,
                avg_tokens=3000,
                created_at="2026-01-01",
            )
        ]
        learner._save_patterns(patterns)
        loaded = learner.load_patterns()
        assert len(loaded) == 1
        assert loaded[0].category == "coding"
        assert loaded[0].confidence == 0.9

    def test_get_injection_text_filters_low_confidence(self, tmp_path):
        learner = PatternLearner(MagicMock(), data_dir=tmp_path)
        patterns = [
            ToolPattern(category="a", pattern="good", confidence=0.9, evidence_count=10),
            ToolPattern(category="b", pattern="bad", confidence=0.3, evidence_count=2),
        ]
        learner._save_patterns(patterns)
        text = learner.get_injection_text()
        assert "good" in text
        assert "bad" not in text


class TestExecutorGuards:
    @pytest.mark.asyncio
    async def test_benchmark_evolve_no_agent(self):
        from openakita.scheduler.executor import TaskExecutor

        executor = TaskExecutor()
        assert executor.agent is None
        success, msg = await executor._system_benchmark_evolve()
        assert success is False
        assert "not available" in msg

    @pytest.mark.asyncio
    async def test_pattern_learn_no_agent(self):
        from openakita.scheduler.executor import TaskExecutor

        executor = TaskExecutor()
        success, msg = await executor._system_pattern_learn()
        assert success is False

    @pytest.mark.asyncio
    async def test_research_org_no_agent(self):
        from openakita.scheduler.executor import TaskExecutor

        executor = TaskExecutor()
        success, msg = await executor._system_research_org()
        assert success is False
