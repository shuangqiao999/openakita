"""
Prompt 自主优化器

分析性能数据 → 生成 prompt 变体 → benchmark 验证 → 保留/回滚

改进:
- P0-1: 采纳决策复用 ExperimentLoop._is_improvement（成功率硬约束+加权公式）
- P0-2: 模糊匹配替换复用 ExperimentLoop._fuzzy_match_and_replace
- P1-3: 变更比例校验增加最小长度
- P1-4: 语法验证复用 ExperimentLoop._validate_syntax + 模板变量检查
- P1-5: 性能数据优先读最新 result
- P2-6: asyncio.Lock 并发控制
- P2-7: 配置化参数
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CHANGE_RATIO = 0.2
_DEFAULT_IMPROVEMENT_THRESHOLD = 0.05


def _parse_llm_json(text: str) -> Any:
    from . import strip_json_fences

    return json.loads(strip_json_fences(text))


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
        "identity/POLICIES.yaml",
    ]

    _evolve_lock = asyncio.Lock()

    def __init__(
        self,
        agent: Any,
        data_dir: str | Path = "data/evolution/prompt_variants",
        *,
        project_root: Path | None = None,
    ) -> None:
        self._agent = agent
        self._brain = getattr(agent, "brain", None)
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        (self._data_dir / "archive").mkdir(parents=True, exist_ok=True)

        if project_root is not None:
            self._project_root = project_root
        else:
            try:
                from ..config import settings

                self._project_root = settings.project_root
            except Exception:
                self._project_root = Path(".")

    def _get_config(self, key: str, default: Any) -> Any:
        try:
            from ..config import settings

            v = getattr(settings, key, default)
            if v is None or (isinstance(v, (int, float)) and v <= 0):
                return default
            return v
        except Exception:
            return default

    async def evolve_step(self, performance_data: dict | None = None) -> VariantResult | None:
        async with PromptOptimizer._evolve_lock:
            return await self._evolve_step_locked(performance_data)

    async def _evolve_step_locked(
        self, performance_data: dict | None = None
    ) -> VariantResult | None:
        if not self._brain:
            return None

        if performance_data is None:
            performance_data = self._collect_recent_performance()

        variant = await self._propose_optimization(performance_data)
        if not variant:
            return None

        max_ratio = self._get_config("prompt_max_change_ratio", _DEFAULT_MAX_CHANGE_RATIO)
        if not self._validate_change_ratio(variant, max_ratio):
            logger.warning("[PromptOptimizer] 变更比例超过 %.0f%% 上限，跳过", max_ratio * 100)
            return VariantResult(variant=variant, reason="变更比例超限")

        result = await self._test_variant(variant, performance_data)
        return result

    async def _propose_optimization(self, perf: dict) -> PromptVariant | None:
        sections_content = {}
        for section in self.OPTIMIZABLE_SECTIONS:
            p = self._project_root / section
            if p.exists():
                sections_content[section] = p.read_text(encoding="utf-8")[:3000]

        if not sections_content:
            return None

        prompt = f"""你是系统 prompt 优化专家。当前 Agent 性能:
- 成功率: {perf.get("success_rate", 0):.1%}
- 平均 token: {perf.get("avg_tokens", 0):.0f}
- 效率分: {perf.get("efficiency_score", 0):.1f}
- 常见失败模式: {perf.get("failure_patterns", "无数据")}

当前 prompt 内容片段:
{json.dumps(sections_content, ensure_ascii=False)[:2000]}

请提出一个小范围的 prompt 改进（只修改一个连续片段，不超过原文 20%），目标是:
1. 减少不必要的 token 消耗
2. 提高工具选择准确性
3. 减少循环和重复

输出 JSON:
{{
    "section": "文件路径",
    "original": "被替换的原始片段（尽量精确匹配原文）",
    "proposed": "替换后的新内容",
    "hypothesis": "为什么这个改动会有效"
}}

如果当前 prompt 已经很好无需修改，返回 {{"skip": true}}
"""
        try:
            llm_timeout = self._get_config("experiment_llm_timeout", 600)
            response = await asyncio.wait_for(self._brain.chat_simple(prompt), timeout=llm_timeout)
            data = _parse_llm_json(response)
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

    def _validate_change_ratio(
        self, variant: PromptVariant, max_ratio: float = _DEFAULT_MAX_CHANGE_RATIO
    ) -> bool:
        p = self._project_root / variant.section
        if not p.exists():
            return False
        full_content = p.read_text(encoding="utf-8")
        touched_ratio = len(variant.original) / max(len(full_content), 1)
        if touched_ratio > max_ratio:
            return False
        if len(variant.proposed) < 10:
            return False
        return True

    async def _test_variant(self, variant: PromptVariant, baseline_perf: dict) -> VariantResult:
        from .experiment_loop import ExperimentLoop

        if variant.section not in self.OPTIMIZABLE_SECTIONS:
            return VariantResult(variant=variant, reason="目标不在允许列表中")

        target_path = (self._project_root / variant.section).resolve()
        if not target_path.is_relative_to(self._project_root.resolve()):
            return VariantResult(variant=variant, reason="路径遍历")
        if not target_path.exists():
            return VariantResult(variant=variant, reason="文件不存在")

        original_full = target_path.read_text(encoding="utf-8")

        new_content, match_err = ExperimentLoop._fuzzy_match_and_replace(
            original_full, variant.original, variant.proposed
        )
        if new_content is None:
            logger.warning("[PromptOptimizer] 匹配失败: %s", match_err)
            return VariantResult(variant=variant, reason=match_err)

        valid, syntax_err = ExperimentLoop._validate_syntax(target_path, new_content)
        if not valid:
            logger.warning("[PromptOptimizer] 语法验证失败: %s", syntax_err)
            return VariantResult(variant=variant, reason=f"语法验证失败: {syntax_err}")

        if target_path.suffix.lower() == ".md":
            tpl_ok, tpl_err = self._validate_template_vars(new_content)
            if not tpl_ok:
                return VariantResult(variant=variant, reason=tpl_err)

        target_path.write_text(new_content, encoding="utf-8")
        try:
            from .benchmark import BenchmarkEngine

            engine = BenchmarkEngine()
            report = await engine.run_suite(self._agent)
            new_metrics = {
                "success_rate": report.metrics.success_rate,
                "avg_tokens": report.metrics.avg_tokens,
                "avg_time": report.metrics.avg_time,
                "efficiency_score": report.metrics.efficiency_score,
            }

            baseline_metrics = {
                "success_rate": baseline_perf.get("success_rate", 0),
                "avg_tokens": baseline_perf.get("avg_tokens", 0),
                "avg_time": baseline_perf.get("avg_time", 0),
                "efficiency_score": baseline_perf.get("efficiency_score", 0),
            }

            threshold = self._get_config(
                "prompt_improvement_threshold", _DEFAULT_IMPROVEMENT_THRESHOLD
            )
            adopted = ExperimentLoop._is_improvement(baseline_metrics, new_metrics, threshold)

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
                reason="" if adopted else "指标未达到改进阈值",
            )
        except asyncio.CancelledError:
            target_path.write_text(original_full, encoding="utf-8")
            raise
        except Exception as e:
            target_path.write_text(original_full, encoding="utf-8")
            return VariantResult(variant=variant, reason=f"测试异常: {e}")

    @staticmethod
    def _validate_template_vars(content: str) -> tuple[bool, str]:
        opens = len(re.findall(r"\{\{", content))
        closes = len(re.findall(r"\}\}", content))
        if opens != closes:
            return False, f"模板变量不平衡: {{{{ {opens}个 vs }}}} {closes}个"
        return True, ""

    def _archive_variant(self, variant: PromptVariant, metrics: dict, adopted: bool) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        status = "adopted" if adopted else "rejected"
        path = self._data_dir / "archive" / f"{ts}_{status}.json"
        data = {
            "section": variant.section,
            "hypothesis": variant.hypothesis,
            "adopted": adopted,
            "metrics": metrics,
            "original": variant.original,
            "proposed": variant.proposed,
            "proposed_length": len(variant.proposed),
            "original_length": len(variant.original),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _collect_recent_performance(self) -> dict:
        from .benchmark import BenchmarkEngine

        engine = BenchmarkEngine()
        results_dir = engine._results_dir
        result_files = sorted(results_dir.glob("*.json"), reverse=True)
        if result_files:
            try:
                data = json.loads(result_files[0].read_text(encoding="utf-8"))
                m = data.get("metrics", {})
                return {
                    "success_rate": m.get("success_rate", 0),
                    "avg_tokens": m.get("avg_tokens", 0),
                    "avg_time": m.get("avg_time", 0),
                    "efficiency_score": m.get("efficiency_score", 0),
                    "failure_patterns": "来自最近 benchmark",
                }
            except Exception:
                pass

        baseline = engine._load_latest_baseline()
        if baseline:
            return {
                "success_rate": baseline.success_rate,
                "avg_tokens": baseline.avg_tokens,
                "avg_time": baseline.avg_time,
                "efficiency_score": baseline.efficiency_score,
                "failure_patterns": "来自 baseline",
            }
        return {
            "success_rate": 0,
            "avg_tokens": 0,
            "avg_time": 0,
            "efficiency_score": 0,
            "failure_patterns": "无历史数据",
        }
