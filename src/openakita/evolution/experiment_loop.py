"""
实验循环 (autoresearch 核心思路移植)

修改一个变量 → 运行 benchmark → 对比指标 → 保留/回滚 → 重复
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IMPROVEMENT_THRESHOLD = 0.02
MAX_EXPERIMENTS_PER_CYCLE = 3


@dataclass
class Hypothesis:
    target: str  # 修改目标（文件路径或参数名）
    description: str
    original_content: str
    proposed_content: str
    rationale: str


@dataclass
class ExperimentResult:
    action: str  # "keep" / "discard" / "error"
    hypothesis: Hypothesis | None = None
    baseline_metrics: dict[str, float] = field(default_factory=dict)
    new_metrics: dict[str, float] = field(default_factory=dict)
    delta: dict[str, float] = field(default_factory=dict)
    reason: str = ""


class ExperimentLoop:
    MUTABLE_TARGETS = [
        "identity/AGENT.md",
        "identity/POLICIES.yaml",
    ]

    def __init__(self, agent: Any, data_dir: str | Path = "data/evolution/experiments") -> None:
        self._agent = agent
        self._brain = getattr(agent, "brain", None)
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._baselines_dir = self._data_dir / "baselines"
        self._baselines_dir.mkdir(parents=True, exist_ok=True)

    async def run_cycle(self, benchmark_report: Any = None) -> list[ExperimentResult]:
        from .benchmark import BenchmarkEngine

        engine = BenchmarkEngine()
        if benchmark_report is None:
            benchmark_report = await engine.run_suite(self._agent)

        baseline_metrics = {
            "success_rate": benchmark_report.metrics.success_rate,
            "avg_tokens": benchmark_report.metrics.avg_tokens,
            "avg_time": benchmark_report.metrics.avg_time,
            "efficiency_score": benchmark_report.metrics.efficiency_score,
        }

        results: list[ExperimentResult] = []
        for _ in range(MAX_EXPERIMENTS_PER_CYCLE):
            hypothesis = await self._generate_hypothesis(baseline_metrics, results)
            if not hypothesis:
                break
            result = await self._run_experiment(hypothesis, engine, baseline_metrics)
            results.append(result)
            if result.action == "keep":
                baseline_metrics = result.new_metrics

        self._save_cycle(results)
        return results

    async def _generate_hypothesis(
        self,
        current_metrics: dict[str, float],
        prior_results: list[ExperimentResult],
    ) -> Hypothesis | None:
        if not self._brain:
            return None

        prior_summary = ""
        if prior_results:
            prior_summary = "\n".join(
                f"- {r.hypothesis.description if r.hypothesis else '?'}: {r.action}"
                for r in prior_results
            )

        targets_content = {}
        from ..config import settings

        for target in self.MUTABLE_TARGETS:
            p = settings.project_root / target
            if p.exists():
                targets_content[target] = p.read_text(encoding="utf-8")[:2000]

        targets_display = "\n\n".join(
            f"### {name}\n```\n{content}\n```" for name, content in targets_content.items()
        )

        prompt = f"""你是一个 AI 系统优化研究员。当前系统性能指标:
- 成功率: {current_metrics.get("success_rate", 0):.1%}
- 平均 token: {current_metrics.get("avg_tokens", 0):.0f}
- 平均耗时: {current_metrics.get("avg_time", 0):.1f}s
- 效率分: {current_metrics.get("efficiency_score", 0):.1f}

{f"已尝试的实验: {prior_summary}" if prior_summary else ""}

可修改的目标文件及当前内容:
{targets_display}

请提出一个具体的改进假设。输出 JSON:
{{
    "target": "目标文件路径（必须从上面的文件中选择）",
    "description": "改进描述（一句话）",
    "rationale": "为什么这个改动会提升性能",
    "proposed_change": "具体的修改内容（完整替换片段）",
    "original_fragment": "被替换的原始片段（必须在原文件中精确存在）"
}}

如果没有好的改进思路，返回 {{"skip": true}}
"""
        try:
            response = await self._brain.chat_simple(prompt)
            data = json.loads(response)
            if data.get("skip"):
                return None
            target = data["target"]
            return Hypothesis(
                target=target,
                description=data["description"],
                original_content=data.get("original_fragment", ""),
                proposed_content=data.get("proposed_change", ""),
                rationale=data["rationale"],
            )
        except Exception as e:
            logger.warning("[ExperimentLoop] 假设生成失败: %s", e)
            return None

    async def _run_experiment(
        self,
        hypothesis: Hypothesis,
        engine: Any,
        baseline_metrics: dict[str, float],
    ) -> ExperimentResult:
        from ..config import settings

        if hypothesis.target not in self.MUTABLE_TARGETS:
            return ExperimentResult(
                action="error", hypothesis=hypothesis, reason="目标不在允许列表中"
            )

        target_path = (settings.project_root / hypothesis.target).resolve()
        project_root_resolved = settings.project_root.resolve()
        if not target_path.is_relative_to(project_root_resolved):
            return ExperimentResult(
                action="error", hypothesis=hypothesis, reason="路径遍历检测: 目标在项目外"
            )

        if not target_path.exists():
            return ExperimentResult(action="error", hypothesis=hypothesis, reason="目标文件不存在")

        original_full = target_path.read_text(encoding="utf-8")

        if hypothesis.proposed_content:
            touched_ratio = len(hypothesis.original_content) / max(len(original_full), 1)
            if touched_ratio > 0.3:
                return ExperimentResult(
                    action="error", hypothesis=hypothesis, reason="替换区域超过文件 30%"
                )
            if len(hypothesis.proposed_content) < 10:
                return ExperimentResult(
                    action="error", hypothesis=hypothesis, reason="替换内容过短"
                )

        backup_path = self._data_dir / f"backup_{target_path.name}_{int(time.time())}"
        backup_path.write_text(original_full, encoding="utf-8")

        try:
            if hypothesis.original_content and hypothesis.proposed_content:
                new_content = original_full.replace(
                    hypothesis.original_content, hypothesis.proposed_content, 1
                )
                if new_content == original_full:
                    return ExperimentResult(
                        action="error", hypothesis=hypothesis, reason="未找到替换目标"
                    )
                target_path.write_text(new_content, encoding="utf-8")
            else:
                return ExperimentResult(
                    action="error", hypothesis=hypothesis, reason="修改内容为空"
                )

            report = await engine.run_suite(self._agent)
            new_metrics = {
                "success_rate": report.metrics.success_rate,
                "avg_tokens": report.metrics.avg_tokens,
                "avg_time": report.metrics.avg_time,
                "efficiency_score": report.metrics.efficiency_score,
            }

            if self._is_improvement(baseline_metrics, new_metrics):
                logger.info("[ExperimentLoop] ✓ 保留改进: %s", hypothesis.description)
                return ExperimentResult(
                    action="keep",
                    hypothesis=hypothesis,
                    baseline_metrics=baseline_metrics,
                    new_metrics=new_metrics,
                    delta={k: new_metrics[k] - baseline_metrics[k] for k in baseline_metrics},
                )
            else:
                target_path.write_text(original_full, encoding="utf-8")
                logger.info("[ExperimentLoop] ✗ 回滚: %s", hypothesis.description)
                return ExperimentResult(
                    action="discard",
                    hypothesis=hypothesis,
                    baseline_metrics=baseline_metrics,
                    new_metrics=new_metrics,
                    delta={k: new_metrics[k] - baseline_metrics[k] for k in baseline_metrics},
                    reason="指标未改善",
                )
        except Exception as e:
            target_path.write_text(original_full, encoding="utf-8")
            return ExperimentResult(action="error", hypothesis=hypothesis, reason=str(e))
        finally:
            if backup_path.exists():
                backup_path.unlink(missing_ok=True)

    def _is_improvement(self, old: dict[str, float], new: dict[str, float]) -> bool:
        sr_old = old.get("success_rate", 0)
        sr_new = new.get("success_rate", 0)
        tok_old = old.get("avg_tokens", 1)
        tok_new = new.get("avg_tokens", 1)
        time_old = old.get("avg_time", 1)
        time_new = new.get("avg_time", 1)

        score_delta = (
            0.5 * (sr_new - sr_old)
            + 0.3 * (tok_old - tok_new) / max(tok_old, 1)
            + 0.2 * (time_old - time_new) / max(time_old, 1)
        )
        return score_delta > IMPROVEMENT_THRESHOLD

    def _save_cycle(self, results: list[ExperimentResult]) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._data_dir / f"{ts}_cycle.json"
        data = [
            {
                "action": r.action,
                "description": r.hypothesis.description if r.hypothesis else "",
                "delta": r.delta,
                "reason": r.reason,
            }
            for r in results
        ]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
