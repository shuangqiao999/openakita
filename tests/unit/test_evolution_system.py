"""
功能测试: 自进化系统 (P0-P4)

覆盖:
1. AutoEvolver 基本逻辑
2. BenchmarkEngine 任务加载和指标计算
3. ExperimentLoop 安全护栏 (路径校验/内容校验/回滚)
4. ResearchOrg 路径白名单校验
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

    def test_dedup_cache(self):
        evolver = AutoEvolver(MagicMock())
        assert not evolver._is_recently_processed("test_cap")
        evolver._mark_processed("test_cap")
        assert evolver._is_recently_processed("test_cap")

    def test_skill_exists_check(self):
        agent = MagicMock()
        agent.skill_registry = MagicMock()
        agent.skill_registry.get_skill = MagicMock(return_value=MagicMock())
        evolver = AutoEvolver(agent)
        assert evolver._skill_exists("existing_skill") is True

    def test_skill_not_exists(self):
        agent = MagicMock()
        agent.skill_registry = MagicMock()
        agent.skill_registry.get_skill = MagicMock(return_value=None)
        evolver = AutoEvolver(agent)
        assert evolver._skill_exists("new_skill") is False

    def test_dependency_injection(self):
        mock_installer = MagicMock()
        mock_skill_gen = MagicMock()
        mock_analyzer = MagicMock()
        evolver = AutoEvolver(
            MagicMock(),
            installer=mock_installer,
            skill_gen=mock_skill_gen,
            need_analyzer=mock_analyzer,
        )
        assert evolver._get_installer() is mock_installer
        assert evolver._get_analyzer() is mock_analyzer
        assert evolver._skill_gen is mock_skill_gen

    @pytest.mark.asyncio
    async def test_analysis_null_protection(self):
        mock_analyzer = MagicMock()
        mock_analyzer.analyze_task = AsyncMock(return_value=None)
        agent = MagicMock()
        agent.brain = MagicMock()
        evolver = AutoEvolver(agent, need_analyzer=mock_analyzer)
        result = await evolver.respond_to_failure(
            task_description="test", harness_gap="missing_tool"
        )
        assert result.action == "skip"
        assert "为空" in result.reason


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

    def test_compute_metrics_with_results(self, tmp_path):
        from openakita.evolution.benchmark import BenchmarkResult

        engine = BenchmarkEngine(data_dir=tmp_path / "bench_metrics")
        tasks = [BenchmarkTask(id="t1", description="", category="test", expected_outcome="")]
        results = [BenchmarkResult(task_id="t1", success=True, tokens_used=1000, time_seconds=5.0)]
        metrics = engine._compute_metrics(results, tasks)
        assert metrics.success_rate == 1.0
        assert metrics.avg_tokens == 1000.0
        assert metrics.efficiency_score == 100.0

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

    @pytest.mark.asyncio
    async def test_timeout_marks_failure(self):
        async def slow_runner(agent, desc):
            await asyncio.sleep(999)

        engine = BenchmarkEngine(task_runner=slow_runner, token_counter=lambda a: 0)
        task = BenchmarkTask(
            id="timeout-test", description="slow", category="test",
            expected_outcome="", timeout_seconds=1,
        )
        result = await engine._run_single(MagicMock(), task)
        assert result.success is False
        assert "超时" in (result.error or "")
        assert result.time_seconds >= 0.9

    @pytest.mark.asyncio
    async def test_verify_outcome_keyword_match(self):
        engine = BenchmarkEngine()
        task = BenchmarkTask(
            id="v1", description="", category="test",
            expected_outcome="输出结果为 '4950'",
        )
        ok, _ = engine._verify_outcome(task, "计算结果是 4950，完成")
        assert ok is True

        fail, reason = engine._verify_outcome(task, "计算完成，结果是 1234")
        assert fail is False
        assert "4950" in reason

    @pytest.mark.asyncio
    async def test_false_positive_caught_by_verification(self):
        async def fake_runner(agent, desc):
            return MagicMock(success=True, data="我无法完成这个任务", iterations=1)

        engine = BenchmarkEngine(task_runner=fake_runner, token_counter=lambda a: 0)
        task = BenchmarkTask(
            id="fp-test", description="test", category="test",
            expected_outcome="输出 'BenchMark2026'", timeout_seconds=5,
        )
        result = await engine._run_single(MagicMock(), task)
        assert result.success is False
        assert result.verification_passed is False

    @pytest.mark.asyncio
    async def test_concurrent_execution_faster_than_serial(self):
        call_count = 0

        async def slow_runner(agent, desc):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.3)
            return MagicMock(success=True, data="ok", iterations=1)

        engine = BenchmarkEngine(task_runner=slow_runner, token_counter=lambda a: 0, max_concurrent=3)
        tasks = [
            BenchmarkTask(id=f"c{i}", description="", category="test",
                          expected_outcome="", timeout_seconds=5)
            for i in range(3)
        ]
        t0 = asyncio.get_event_loop().time()
        report = await engine.run_suite(MagicMock(), tasks)
        elapsed = asyncio.get_event_loop().time() - t0
        assert call_count == 3
        assert report.metrics.success_rate == 1.0
        assert elapsed < 1.0

    def test_auto_save_baseline_first_run(self, tmp_path):
        import asyncio as _aio

        async def fake_runner(agent, desc):
            return MagicMock(success=True, data="ok", iterations=1)

        engine = BenchmarkEngine(
            data_dir=tmp_path / "bench_auto",
            task_runner=fake_runner,
            token_counter=lambda a: 0,
        )
        assert engine._load_latest_baseline() is None
        tasks = [
            BenchmarkTask(id=f"auto-{i}", description="test", category="test",
                          expected_outcome="", timeout_seconds=5)
            for i in range(3)
        ]
        report = _aio.run(engine.run_suite(MagicMock(), tasks))
        assert report.metrics.success_rate == 1.0
        loaded = engine._load_latest_baseline()
        assert loaded is not None
        assert loaded.success_rate == 1.0

    def test_custom_task_runner_called(self):
        import asyncio as _aio

        called_with = []

        async def my_runner(agent, desc):
            called_with.append(desc)
            return MagicMock(success=True, data="custom", iterations=0)

        engine = BenchmarkEngine(task_runner=my_runner, token_counter=lambda a: 0)
        tasks = [BenchmarkTask(id="cr", description="hello", category="test",
                               expected_outcome="", timeout_seconds=5)]
        _aio.run(engine.run_suite(MagicMock(), tasks))
        assert called_with == ["hello"]

    def test_custom_token_counter(self):
        import asyncio as _aio

        async def runner(agent, desc):
            return MagicMock(success=True, data="ok", iterations=0)

        counter_calls = []

        def my_counter(agent):
            counter_calls.append(1)
            return len(counter_calls) * 500

        engine = BenchmarkEngine(task_runner=runner, token_counter=my_counter)
        tasks = [BenchmarkTask(id="tc", description="", category="test",
                               expected_outcome="", timeout_seconds=5)]
        report = _aio.run(engine.run_suite(MagicMock(), tasks))
        assert len(counter_calls) == 2
        assert report.results[0].tokens_used == 500


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
        loop = ExperimentLoop(mock_agent, project_root=tmp_path)
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
        target_file = tmp_path / "identity" / "AGENT.md"
        target_file.parent.mkdir(parents=True)
        target_file.write_text("short content", encoding="utf-8")

        loop = ExperimentLoop(mock_agent, data_dir=tmp_path / "exp", project_root=tmp_path)
        hypothesis = Hypothesis(
            target="identity/AGENT.md",
            description="test",
            original_content="short content",
            proposed_content="replacement text here",
            rationale="test",
        )
        result = await loop._run_experiment(hypothesis, MagicMock(), {})
        assert result.action == "error"
        assert "30%" in result.reason

    @pytest.mark.asyncio
    async def test_rejects_short_replacement(self, mock_agent, tmp_path):
        target_file = tmp_path / "identity" / "AGENT.md"
        target_file.parent.mkdir(parents=True)
        target_file.write_text("x" * 1000, encoding="utf-8")

        loop = ExperimentLoop(mock_agent, data_dir=tmp_path / "exp", project_root=tmp_path)
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

    def test_fuzzy_match_whitespace_difference(self):
        original = "line one\n  line  two\nline three\n"
        fragment = "line one\nline two\nline three\n"
        replacement = "REPLACED\n"
        result, err = ExperimentLoop._fuzzy_match_and_replace(original, fragment, replacement)
        assert result is not None
        assert "REPLACED" in result

    def test_fuzzy_match_exact(self):
        original = "hello world"
        result, err = ExperimentLoop._fuzzy_match_and_replace(original, "hello", "HI")
        assert result == "HI world"

    def test_fuzzy_match_no_match(self):
        original = "hello world"
        result, err = ExperimentLoop._fuzzy_match_and_replace(original, "completely different text here", "x")
        assert result is None
        assert "无法匹配" in err

    def test_syntax_validation_python_valid(self):
        ok, _ = ExperimentLoop._validate_syntax(Path("test.py"), "x = 1\nprint(x)\n")
        assert ok is True

    def test_syntax_validation_python_invalid(self):
        ok, reason = ExperimentLoop._validate_syntax(Path("test.py"), "def foo(\n")
        assert ok is False
        assert "语法错误" in reason

    def test_syntax_validation_yaml_valid(self):
        ok, _ = ExperimentLoop._validate_syntax(Path("test.yaml"), "key: value\n")
        assert ok is True

    def test_syntax_validation_markdown_always_valid(self):
        ok, _ = ExperimentLoop._validate_syntax(Path("test.md"), "# bad {{{{ syntax")
        assert ok is True

    def test_success_rate_hard_constraint(self):
        old = {"success_rate": 0.8, "avg_tokens": 5000, "avg_time": 10}
        new_worse_sr = {"success_rate": 0.6, "avg_tokens": 1000, "avg_time": 3}
        assert ExperimentLoop._is_improvement(old, new_worse_sr) is False

    def test_improvement_accepted_when_sr_equal(self):
        old = {"success_rate": 0.8, "avg_tokens": 5000, "avg_time": 10}
        new_better = {"success_rate": 0.8, "avg_tokens": 2000, "avg_time": 5}
        assert ExperimentLoop._is_improvement(old, new_better) is True

    def test_backup_cleanup(self, tmp_path):
        loop = ExperimentLoop(MagicMock(), data_dir=tmp_path / "exp", project_root=tmp_path)
        backups = loop._backups_dir
        old_backup = backups / "backup_old_123"
        old_backup.write_text("old", encoding="utf-8")
        import os
        os.utime(old_backup, (0, 0))
        recent_backup = backups / "backup_recent_456"
        recent_backup.write_text("recent", encoding="utf-8")
        loop._cleanup_old_backups(max_age_days=1)
        assert not old_backup.exists()
        assert recent_backup.exists()

    @pytest.mark.asyncio
    async def test_concurrent_lock(self, mock_agent, tmp_path):
        call_order = []

        async def fake_suite(agent, **kw):
            call_order.append("start")
            await asyncio.sleep(0.2)
            call_order.append("end")
            return MagicMock(
                metrics=MagicMock(success_rate=1.0, avg_tokens=0, avg_time=0, efficiency_score=100)
            )

        from openakita.evolution.benchmark import BenchmarkEngine
        with patch.object(BenchmarkEngine, "run_suite", side_effect=fake_suite):
            loop = ExperimentLoop(mock_agent, data_dir=tmp_path / "exp", project_root=tmp_path)
            loop._brain = None
            t1 = asyncio.create_task(loop.run_cycle())
            t2 = asyncio.create_task(loop.run_cycle())
            await asyncio.gather(t1, t2)
        assert call_order == ["start", "end", "start", "end"]


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
        success, _ = await org._apply_prompt_change(proposal, {})
        assert success is False

    @pytest.mark.asyncio
    async def test_concurrent_lock(self, mock_agent):
        call_order = []

        def fake_gather(self_ref):
            return {"metrics": {"success_rate": 0.5}, "failures": [], "tool_stats": {}}

        async def fake_analyst(self_ref, data, timeout=60):
            call_order.append("start")
            await asyncio.sleep(0.2)
            call_order.append("end")
            return []

        with patch.object(ResearchOrg, "_gather_performance_data", fake_gather):
            with patch.object(ResearchOrg, "_run_analyst", fake_analyst):
                org = ResearchOrg(mock_agent)
                t1 = asyncio.create_task(org.run_research_cycle())
                t2 = asyncio.create_task(org.run_research_cycle())
                await asyncio.gather(t1, t2)
        assert call_order == ["start", "end", "start", "end"]

    def test_gather_performance_data_structure(self, mock_agent):
        org = ResearchOrg(mock_agent)
        data = org._gather_performance_data()
        assert "metrics" in data
        assert "failures" in data
        assert "tool_stats" in data
        assert isinstance(data["failures"], list)
        assert isinstance(data["tool_stats"], dict)


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

    def test_extract_tool_names_nested(self):
        data = {
            "iterations": [
                {"tool_name": "read_file", "result": "ok"},
                {"tool_calls": [
                    {"name": "grep", "tool_input": {"q": "x"}, "tool_call_id": "1"},
                    {"name": "edit_file", "arguments": {}, "tool_call_id": "2"},
                ]},
                {"metadata": {"tool": "run_shell"}},
            ]
        }
        tools = PatternLearner._extract_tool_names(data["iterations"])
        assert "read_file" in tools
        assert "grep" in tools
        assert "edit_file" in tools
        assert "run_shell" in tools

    def test_extract_tool_names_flat(self):
        data = [{"tool_name": "web_search"}, {"tool": "web_fetch"}]
        tools = PatternLearner._extract_tool_names(data)
        assert tools == ["web_search", "web_fetch"]

    def test_efficient_cluster_uses_median(self):
        from openakita.evolution.pattern_learner import ToolSequence as TS
        seqs = [
            TS(task_category="test", tools=["a"], tokens_used=100, success=True, time_seconds=1),
            TS(task_category="test", tools=["b"], tokens_used=500, success=True, time_seconds=2),
            TS(task_category="test", tools=["c"], tokens_used=1000, success=True, time_seconds=3),
            TS(task_category="test", tools=["d"], tokens_used=2000, success=True, time_seconds=5),
            TS(task_category="test", tools=["e"], tokens_used=3000, success=True, time_seconds=8),
        ]
        learner = PatternLearner(MagicMock(), data_dir="/tmp/pl_test")
        clusters = {"test": seqs}
        efficient = learner._find_efficient_clusters(clusters)
        assert "test" in efficient
        for s in efficient["test"]:
            assert s.tokens_used <= 1000

    def test_injection_text_truncation(self, tmp_path):
        learner = PatternLearner(MagicMock(), data_dir=tmp_path)
        patterns = [
            ToolPattern(category=f"cat{i}", pattern="x" * 150, confidence=0.9, evidence_count=10)
            for i in range(5)
        ]
        learner._save_patterns(patterns)
        text = learner.get_injection_text(max_chars=500)
        assert len(text) <= 510

    def test_jaccard_dedup(self, tmp_path):
        learner = PatternLearner(MagicMock(), data_dir=tmp_path)
        patterns = [
            ToolPattern(category="a", pattern="grep read_file edit_file run_shell check", confidence=0.9, evidence_count=10),
            ToolPattern(category="b", pattern="grep edit_file read_file run_shell check lints", confidence=0.7, evidence_count=5),
        ]
        result = learner._deduplicate_patterns(patterns)
        assert len(result) == 1
        assert result[0].confidence == 0.9

    def test_incremental_learning_state(self, tmp_path):
        learner = PatternLearner(MagicMock(), data_dir=tmp_path)
        assert learner._load_learn_state() == 0.0
        learner._save_learn_state(12345.0)
        assert learner._load_learn_state() == 12345.0


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
