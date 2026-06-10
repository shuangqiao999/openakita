"""
自动进化响应器

任务失败时自动分析缺失能力并尝试补全:
1. 能力差距分析 (NeedAnalyzer)
2. 依赖自动安装 (AutoInstaller)
3. 技能自动生成 (SkillGenerator)

改进:
- P0-1: 去重缓存（5分钟TTL防止重复处理同一能力）
- P0-2: 技能存在检查（避免覆盖已有技能）
- P1-3: 依赖注入（installer/skill_gen/need_analyzer 可 mock）
- P1-4: 异常捕获细化（分阶段错误记录）
- P1-5: 空值保护（analysis 为 None 时安全返回）
- P2-6: gen_result 安全属性访问
- P2-7: result.method 安全访问
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

EVOLVABLE_GAPS = frozenset({"missing_tool", "insufficient_docs"})
_DEDUP_TTL_S = 300


@dataclass
class EvolutionResult:
    action: str  # "skip" / "evolved" / "partial" / "failed"
    reason: str = ""
    installed: list[Any] = field(default_factory=list)
    generated: list[Any] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class AutoEvolver:
    _recently_processed: dict[str, float] = {}

    def __init__(
        self,
        agent: Any,
        *,
        installer: Any | None = None,
        skill_gen: Any | None = None,
        need_analyzer: Any | None = None,
    ) -> None:
        self._agent = agent
        self._brain = getattr(agent, "brain", None)
        self._skill_registry = getattr(agent, "skill_registry", None)
        self._installer = installer
        self._skill_gen = skill_gen or getattr(agent, "skill_generator", None)
        self._need_analyzer = need_analyzer

    def _get_analyzer(self) -> Any:
        if self._need_analyzer:
            return self._need_analyzer
        from .analyzer import NeedAnalyzer

        return NeedAnalyzer(
            brain=self._brain,
            skill_registry=self._skill_registry,
        )

    def _get_installer(self) -> Any:
        if self._installer:
            return self._installer
        from .installer import AutoInstaller

        return AutoInstaller()

    def _is_recently_processed(self, name: str) -> bool:
        now = time.monotonic()
        ts = self._recently_processed.get(name)
        if ts is not None and now - ts < _DEDUP_TTL_S:
            return True
        return False

    def _mark_processed(self, name: str) -> None:
        now = time.monotonic()
        self._recently_processed[name] = now
        stale = [k for k, v in self._recently_processed.items() if now - v > _DEDUP_TTL_S]
        for k in stale:
            del self._recently_processed[k]

    def _skill_exists(self, name: str) -> bool:
        if not self._skill_registry:
            return False
        try:
            if hasattr(self._skill_registry, "get_skill"):
                return self._skill_registry.get_skill(name) is not None
            if hasattr(self._skill_registry, "skills"):
                return name in self._skill_registry.skills
        except Exception:
            pass
        return False

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

        errors: list[str] = []

        try:
            analyzer = self._get_analyzer()
        except Exception as e:
            return EvolutionResult(action="failed", errors=[f"分析器初始化失败: {e}"])

        enriched_desc = task_description
        if suggestion:
            enriched_desc = f"{task_description}\n\n失败建议: {suggestion}"

        try:
            analysis = await analyzer.analyze_task(enriched_desc)
        except Exception as e:
            return EvolutionResult(action="failed", errors=[f"能力分析失败: {e}"])

        if not analysis or not getattr(analysis, "missing_capabilities", None):
            return EvolutionResult(action="skip", reason="分析结果为空")

        gaps = [g for g in analysis.missing_capabilities if g.priority >= 7]
        if not gaps:
            return EvolutionResult(action="skip", reason="未识别到高优先级能力缺口")

        installed: list[dict] = []
        generated: list[dict] = []

        auto_installer = self._get_installer()
        for gap in gaps[:3]:
            if self._is_recently_processed(gap.name):
                logger.info("[AutoEvolve] 跳过近期已处理: %s", gap.name)
                continue
            try:
                result = await auto_installer.install_capability(gap)
                if getattr(result, "success", False):
                    method = getattr(result, "method", "unknown")
                    installed.append({"name": gap.name, "method": method})
                    self._mark_processed(gap.name)
                    logger.info("[AutoEvolve] 已安装: %s via %s", gap.name, method)
            except Exception as e:
                errors.append(f"安装失败({gap.name}): {e}")

        remaining = [
            g
            for g in gaps[:3]
            if g.name not in {i["name"] for i in installed}
            and not self._is_recently_processed(g.name)
        ]

        if self._skill_gen and remaining:
            for gap in remaining[:2]:
                if self._skill_exists(gap.name):
                    logger.info("[AutoEvolve] 技能已存在: %s, 跳过", gap.name)
                    self._mark_processed(gap.name)
                    continue
                try:
                    gen_result = await self._skill_gen.generate(gap.description, name=None)
                    if gen_result and getattr(gen_result, "success", False):
                        skill_name = getattr(gen_result, "skill_name", "unknown")
                        generated.append({"name": gap.name, "skill": skill_name})
                        self._mark_processed(gap.name)
                        logger.info("[AutoEvolve] 已生成技能: %s", skill_name)
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
