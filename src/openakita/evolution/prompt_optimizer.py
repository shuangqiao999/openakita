"""
Prompt 自主优化器

分析性能数据 → 生成 prompt 变体 → benchmark 验证 → 保留/回滚
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_CHANGE_RATIO = 0.2


@dataclass
class PromptVariant:
    section: str
    original: str
    proposed: str
    hypothesis: str
    timestamp: str = ""


@dataclass
class VariantResult:
    variant: PromptVariant
    metrics: dict[str, float] = field(default_factory=dict)
    baseline_metrics: dict[str, float] = field(default_factory=dict)
    adopted: bool = False
    reason: str = ""


class PromptOptimizer:
    OPTIMIZABLE_SECTIONS = [
        "identity/AGENT.md",
    ]

    def __init__(self, agent: Any, data_dir: str | Path = "data/evolution/prompt_variants") -> None:
        self._agent = agent
        self._brain = getattr(agent, "brain", None)
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        (self._data_dir / "archive").mkdir(parents=True, exist_ok=True)

    async def evolve_step(self, performance_data: dict | None = None) -> VariantResult | None:
        if not self._brain:
            return None

        if performance_data is None:
            performance_data = self._collect_recent_performance()

        variant = await self._propose_optimization(performance_data)
        if not variant:
            return None

        if not self._validate_change_ratio(variant):
            logger.warning(
                "[PromptOptimizer] 变更比例超过 %.0f%% 上限，跳过", MAX_CHANGE_RATIO * 100
            )
            return VariantResult(variant=variant, reason="变更比例超限")

        result = await self._test_variant(variant, performance_data)
        return result

    async def _propose_optimization(self, perf: dict) -> PromptVariant | None:
        from ..config import settings

        sections_content = {}
        for section in self.OPTIMIZABLE_SECTIONS:
            p = settings.project_root / section
            if p.exists():
                sections_content[section] = p.read_text(encoding="utf-8")[:3000]

        if not sections_content:
            return None

        prompt = f"""你是系统 prompt 优化专家。当前 Agent 性能:
- 成功率: {perf.get("success_rate", 0):.1%}
- 平均 token: {perf.get("avg_tokens", 0):.0f}
- 常见失败模式: {perf.get("failure_patterns", "无数据")}

当前 prompt 内容片段:
{json.dumps(sections_content, ensure_ascii=False)[:2000]}

请提出一个小范围的 prompt 改进（不超过原文 20%），目标是:
1. 减少不必要的 token 消耗
2. 提高工具选择准确性
3. 减少循环和重复

输出 JSON:
{{
    "section": "文件路径",
    "original": "被替换的原始片段（精确匹配）",
    "proposed": "替换后的新内容",
    "hypothesis": "为什么这个改动会有效"
}}

如果当前 prompt 已经很好无需修改，返回 {{"skip": true}}
"""
        try:
            response = await self._brain.chat_simple(prompt)
            data = json.loads(response)
            if data.get("skip"):
                return None
            return PromptVariant(
                section=data["section"],
                original=data["original"],
                proposed=data["proposed"],
                hypothesis=data["hypothesis"],
                timestamp=datetime.now().isoformat(),
            )
        except Exception as e:
            logger.warning("[PromptOptimizer] 提案生成失败: %s", e)
            return None

    def _validate_change_ratio(self, variant: PromptVariant) -> bool:
        from ..config import settings

        p = settings.project_root / variant.section
        if not p.exists():
            return False
        full_content = p.read_text(encoding="utf-8")
        touched_ratio = len(variant.original) / max(len(full_content), 1)
        return touched_ratio <= MAX_CHANGE_RATIO

    async def _test_variant(self, variant: PromptVariant, baseline_perf: dict) -> VariantResult:
        from ..config import settings
        from .benchmark import BenchmarkEngine

        if variant.section not in self.OPTIMIZABLE_SECTIONS:
            return VariantResult(variant=variant, reason="目标不在允许列表中")

        target_path = (settings.project_root / variant.section).resolve()
        if not target_path.is_relative_to(settings.project_root.resolve()):
            return VariantResult(variant=variant, reason="路径遍历")
        if not target_path.exists():
            return VariantResult(variant=variant, reason="文件不存在")

        original_full = target_path.read_text(encoding="utf-8")
        new_content = original_full.replace(variant.original, variant.proposed, 1)
        if new_content == original_full:
            return VariantResult(variant=variant, reason="未找到替换目标")

        target_path.write_text(new_content, encoding="utf-8")
        try:
            engine = BenchmarkEngine()
            report = await engine.run_suite(self._agent)
            new_metrics = {
                "success_rate": report.metrics.success_rate,
                "avg_tokens": report.metrics.avg_tokens,
                "efficiency_score": report.metrics.efficiency_score,
            }

            baseline_metrics = {
                "success_rate": baseline_perf.get("success_rate", 0),
                "avg_tokens": baseline_perf.get("avg_tokens", 0),
                "efficiency_score": baseline_perf.get("efficiency_score", 0),
            }

            improvement = new_metrics.get("efficiency_score", 0) - baseline_metrics.get(
                "efficiency_score", 0
            )
            adopted = improvement > 1.0

            if adopted:
                self._archive_variant(variant, new_metrics, adopted=True)
                logger.info("[PromptOptimizer] ✓ 采纳变体: %s", variant.hypothesis)
            else:
                target_path.write_text(original_full, encoding="utf-8")
                self._archive_variant(variant, new_metrics, adopted=False)
                logger.info("[PromptOptimizer] ✗ 回滚变体: %s", variant.hypothesis)

            return VariantResult(
                variant=variant,
                metrics=new_metrics,
                baseline_metrics=baseline_metrics,
                adopted=adopted,
                reason="" if adopted else "效率未显著提升",
            )
        except Exception as e:
            target_path.write_text(original_full, encoding="utf-8")
            return VariantResult(variant=variant, reason=f"测试异常: {e}")

    def _archive_variant(self, variant: PromptVariant, metrics: dict, adopted: bool) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        status = "adopted" if adopted else "rejected"
        path = self._data_dir / "archive" / f"{ts}_{status}.json"
        data = {
            "section": variant.section,
            "hypothesis": variant.hypothesis,
            "adopted": adopted,
            "metrics": metrics,
            "proposed_length": len(variant.proposed),
            "original_length": len(variant.original),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _collect_recent_performance(self) -> dict:
        from .benchmark import BenchmarkEngine

        engine = BenchmarkEngine()
        baseline = engine._load_latest_baseline()
        if baseline:
            return {
                "success_rate": baseline.success_rate,
                "avg_tokens": baseline.avg_tokens,
                "efficiency_score": baseline.efficiency_score,
                "failure_patterns": "无详细数据",
            }
        return {"success_rate": 0, "avg_tokens": 0, "failure_patterns": "无历史基线"}
