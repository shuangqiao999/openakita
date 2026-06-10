"""
自动进化响应器

任务失败时自动分析缺失能力并尝试补全:
1. 能力差距分析 (NeedAnalyzer)
2. 依赖自动安装 (AutoInstaller)
3. 技能自动生成 (SkillGenerator)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

EVOLVABLE_GAPS = frozenset({"missing_tool", "insufficient_docs"})


@dataclass
class EvolutionResult:
    action: str  # "skip" / "evolved" / "partial" / "failed"
    reason: str = ""
    installed: list[Any] = field(default_factory=list)
    generated: list[Any] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class AutoEvolver:
    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self._brain = getattr(agent, "brain", None)
        self._skill_registry = getattr(agent, "skill_registry", None)

    async def respond_to_failure(
        self,
        task_description: str,
        harness_gap: str,
        suggestion: str = "",
    ) -> EvolutionResult:
        if harness_gap not in EVOLVABLE_GAPS:
            return EvolutionResult(action="skip", reason=f"非可进化类缺口: {harness_gap}")

        if not self._brain:
            return EvolutionResult(action="skip", reason="Brain 不可用")

        try:
            from .analyzer import NeedAnalyzer

            analyzer = NeedAnalyzer(
                brain=self._brain,
                skill_registry=self._skill_registry,
            )
            enriched_desc = task_description
            if suggestion:
                enriched_desc = f"{task_description}\n\n失败建议: {suggestion}"
            analysis = await analyzer.analyze_task(enriched_desc)
            gaps = [g for g in analysis.missing_capabilities if g.priority >= 7]

            if not gaps:
                return EvolutionResult(action="skip", reason="未识别到高优先级能力缺口")

            installed = []
            generated = []
            errors = []

            from .installer import AutoInstaller

            auto_installer = AutoInstaller()
            for gap in gaps[:3]:
                result = await auto_installer.install_capability(gap)
                if result.success:
                    installed.append({"name": gap.name, "method": result.method})
                    logger.info("[AutoEvolve] 已安装: %s via %s", gap.name, result.method)

            remaining = [g for g in gaps[:3] if g.name not in {i["name"] for i in installed}]

            skill_gen = getattr(self._agent, "skill_generator", None)
            if skill_gen and remaining:
                for gap in remaining[:2]:
                    try:
                        gen_result = await skill_gen.generate(gap.description, name=None)
                        if gen_result and gen_result.success:
                            generated.append({"name": gap.name, "skill": gen_result.skill_name})
                            logger.info("[AutoEvolve] 已生成技能: %s", gen_result.skill_name)
                        else:
                            errors.append(f"技能生成失败: {gap.name}")
                    except Exception as e:
                        errors.append(f"技能生成异常({gap.name}): {e}")

            if installed or generated:
                return EvolutionResult(
                    action="evolved",
                    installed=installed,
                    generated=generated,
                    errors=errors,
                )
            elif errors:
                return EvolutionResult(action="failed", errors=errors)
            else:
                return EvolutionResult(action="skip", reason="所有能力缺口均无法自动补全")

        except Exception as e:
            logger.warning("[AutoEvolve] 进化响应异常: %s", e)
            return EvolutionResult(action="failed", errors=[str(e)])
