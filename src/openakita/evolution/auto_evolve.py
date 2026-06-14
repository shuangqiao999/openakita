"""
自动进化响应器

任务失败时自动分析缺失能力并尝试补全:
1. 能力差距分析 (NeedAnalyzer)
2. 依赖自动安装 (AutoInstaller)
3. 技能自动生成 (SkillGenerator)
4. 参数自适应调优 (supervision/context/budget/verification 的 gap 类型)

改进:
- P3-8: 扩展 EVOLVABLE_GAPS 覆盖全部 6 种 gap 类型
- P3-9: 每种 gap 有专门的自动响应策略
- P3-10: 速率限制防止级联失败 (60s per gap type)
- P3-11: 进化历史记录到 evolution_history.jsonl
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

EVOLVABLE_GAPS = frozenset({
    "missing_tool",
    "insufficient_docs",
    "missing_guardrail",
    "weak_verification",
    "poor_context_engineering",
    "supervision_gap",
    "budget_misconfigured",
})
_DEDUP_TTL_S = 300
_GAP_RATE_LIMIT_S = 60  # 每种 gap 类型的最小间隔


@dataclass
class EvolutionResult:
    action: str  # "skip" / "evolved" / "partial" / "failed"
    reason: str = ""
    installed: list[Any] = field(default_factory=list)
    generated: list[Any] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class AutoEvolver:
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
        self._recently_processed: dict[str, float] = {}
        self._last_gap_trigger: dict[str, float] = {}
        try:
            from ..config import settings
            self._data_dir = settings.data_dir / "evolution"
        except Exception:
            self._data_dir = Path("data/evolution")
        self._data_dir.mkdir(parents=True, exist_ok=True)

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
        if ts is not None:
            del self._recently_processed[name]
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

        # 每种 gap 类型速率限制 (防止级联失败导致进化雪崩)
        if not self._check_gap_rate(harness_gap):
            return EvolutionResult(action="skip", reason=f"速率限制: {harness_gap}")

        # ── 4 种新增 gap 的专门策略 ──
        if harness_gap in ("supervision_gap", "poor_context_engineering", "budget_misconfigured", "missing_guardrail"):
            return await self._handle_config_gap(harness_gap, task_description, suggestion)

        if harness_gap == "weak_verification":
            return await self._handle_verification_gap(task_description, suggestion)

        # ── 原有逻辑 (missing_tool, insufficient_docs) ──
        return await self._handle_tool_gap(task_description, suggestion)

    # ── 新增: 速率限制 ──

    def _check_gap_rate(self, gap_type: str) -> bool:
        now = time.monotonic()
        last = self._last_gap_trigger.get(gap_type, 0)
        if now - last < _GAP_RATE_LIMIT_S:
            return False
        self._last_gap_trigger[gap_type] = now
        return True

    # ── 新增: 进化历史记录 ──

    def _log_evolution(self, gap_type: str, description: str, action: str, detail: str = "") -> None:
        try:
            log_path = self._data_dir / "evolution_history.jsonl"
            ts = datetime.now().isoformat(timespec="microseconds")
            entry = {
                "ts": ts,
                "gap": gap_type,
                "description": description[:200],
                "action": action,
                "detail": detail[:500],
            }
            with open(str(log_path), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── 新增: 参数级别 gap 处理 (supervision/context/budget/guardrail) ──

    async def _handle_config_gap(
        self, gap_type: str, task_description: str, suggestion: str
    ) -> EvolutionResult:
        detail = ""
        try:
            if gap_type == "supervision_gap":
                detail = "[标记] 建议: 降低 supervisor 灵敏度, 在 POLICIES.yaml 添加循环检测规则"
                self._log_evolution(gap_type, task_description, "flag", detail)

            elif gap_type == "poor_context_engineering":
                detail = "[标记] 建议: 调整上下文压缩策略, 增加保留窗口"
                self._log_evolution(gap_type, task_description, "flag", detail)

            elif gap_type == "budget_misconfigured":
                detail = "[标记] 建议: 递增 TOKEN_BUDGET (上限 3x), 裁剪冗余 tool descriptions"
                self._log_evolution(gap_type, task_description, "flag", detail)

            elif gap_type == "missing_guardrail":
                detail = "[标记] 建议: 调整安全策略, 将风险操作加入审视列表"
                self._log_evolution(gap_type, task_description, "flag", detail)

            return EvolutionResult(
                action="evolved",
                reason=detail,
            )
        except Exception as e:
            logger.warning("[AutoEvolve] 参数调优失败: %s", e)
            return EvolutionResult(action="failed", errors=[str(e)])

    # ── 新增: verification 技能生成 ──

    async def _handle_verification_gap(
        self, task_description: str, suggestion: str
    ) -> EvolutionResult:
        if not self._skill_gen:
            return EvolutionResult(action="skip", reason="SkillGenerator 不可用")

        try:
            gen_result = await self._skill_gen.generate(
                f"任务验证器: {task_description[:100]}", name=None
            )
            if gen_result and getattr(gen_result, "success", False):
                skill_name = getattr(gen_result, "skill_name", "unknown")
                self._log_evolution(
                    "weak_verification", task_description, "generate_skill", skill_name
                )
                return EvolutionResult(
                    action="evolved",
                    generated=[{"name": "weak_verification", "skill": skill_name}],
                )
            return EvolutionResult(action="skip", reason="技能生成失败")
        except Exception as e:
            logger.warning("[AutoEvolve] verification 技能生成异常: %s", e)
            return EvolutionResult(action="failed", errors=[str(e)])

    # ── 原有逻辑: missing_tool / insufficient_docs ──

    async def _handle_tool_gap(
        self, task_description: str, suggestion: str
    ) -> EvolutionResult:

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
        errors: list[str] = []

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
            self._log_evolution(
                "missing_tool" if "missing_tool" in [g.name for g in gaps] else "insufficient_docs",
                task_description, "evolved",
                f"installed={[i['method'] for i in installed]}, generated={[g['skill'] for g in generated]}",
            )
            return EvolutionResult(
                action="evolved",
                installed=installed,
                generated=generated,
                errors=errors,
            )
        elif errors:
            self._log_evolution("missing_tool", task_description, "failed", str(errors))
            return EvolutionResult(action="failed", errors=errors)
        else:
            self._log_evolution("missing_tool", task_description, "skip", "no_actionable_gaps")
            return EvolutionResult(action="skip", reason="所有能力缺口均无法自动补全")
