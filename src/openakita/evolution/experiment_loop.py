"""
实验循环 (autoresearch 核心思路移植)

修改一个变量 → 运行 benchmark → 对比指标 → 保留/回滚 → 重复

改进:
- P0-1: difflib 鲁棒匹配替换
- P0-2: ast/yaml 语法验证
- P0-3: asyncio.Lock 并发互斥
- P0-4: 备份集中管理 + 定期清理
- P1-5: 成功率不下降硬约束
- P1-6: 历史失败原因反馈给 LLM
- P1-7: LLM 调用超时控制
- P1-8: 配置化参数
- P2-9: project_root 解耦
- P2-10: 备份目录集中管理
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_EXPERIMENTS = 3


def _parse_llm_json(text: str) -> Any:
    from . import strip_json_fences

    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    return json.loads(strip_json_fences(text))


_DEFAULT_IMPROVEMENT_THRESHOLD = 0.02
_DEFAULT_LLM_TIMEOUT = 600
_BACKUP_MAX_AGE_DAYS = 7
_FUZZY_MATCH_THRESHOLD = 0.85
_MAX_REGRESSION_TOLERANCE = 0.10  # 成功率相对原始基线允许的最大退化


@dataclass
class Hypothesis:
    target: str
    description: str
    original_content: str
    proposed_content: str
    rationale: str


@dataclass
class ExperimentResult:
    action: str  # "keep" / "discard" / "error" / "reverted"
    hypothesis: Hypothesis | None = None
    baseline_metrics: dict[str, float] = field(default_factory=dict)
    new_metrics: dict[str, float] = field(default_factory=dict)
    delta: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    quality_score: Any = None
    original_content: str = ""  # 文件实验的原始内容, 用于回归回滚


async def _get_memory_tuning_hint() -> str:
    try:
        from openakita.config import parse_bool, settings

        enabled = parse_bool(getattr(settings, "memory_retrieval_tuning_enabled", True), default=True)
        if not enabled:
            return ""
        try:
            from .runtime_metrics import RuntimeMetricsCollector

            collector = RuntimeMetricsCollector()
        except Exception:
            return ""

        usage_rate = getattr(settings, "memory_usage_low_threshold", 0.3)
        cooldown = getattr(settings, "memory_tuning_cooldown_hours", 24)

        last_tune = collector.get_last_tuning_time()
        if time.time() - last_tune < cooldown * 3600:
            return ""

        snap = await asyncio.to_thread(collector.collect)
        if snap.memory_usage_rate >= usage_rate:
            return ""

        collector.record_tuning_time()
        return (
            f"\n⚠ 记忆使用率: {snap.memory_usage_rate:.0%} (阈值 {usage_rate:.0%})\n"
            "建议调整记忆检索参数以提升召回率:\n"
            "- env:RETRIEVAL_TOP_K (1-20): 增大可召回更多记忆\n"
            "- env:MEMORY_SIMILARITY_THRESHOLD (0.5-0.95): 降低可放宽匹配\n"
        )
    except Exception:
        return ""


def _load_quality_delta(data_dir: str = "data/evolution/experiments/quality_scores") -> float:
    try:
        from .conversation_quality import ConversationQualityEvaluator

        evaluator = ConversationQualityEvaluator(agent=None, data_dir=data_dir)
        avg = evaluator.load_weekly_average(min_samples=1)
        if avg is None:
            return 0.0
        return avg - 0.5
    except Exception:
        return 0.0


def _get_env_targets_display() -> str:
    try:
        from openakita.config import EVOLVABLE_ENV_PARAMS, settings
    except ImportError:
        return ""
    lines = []
    for key, (default_val, lo, hi, restart) in sorted(EVOLVABLE_ENV_PARAMS.items()):
        current = getattr(settings, key.lower(), default_val)
        if isinstance(current, bool):
            current = 1 if current else 0
        if isinstance(default_val, (int, float)) and isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            restart_note = " (需重启)" if restart else ""
            lines.append(f"- env:{key} = {current} (范围 {lo}-{hi}){restart_note}")
        else:
            lines.append(f"- env:{key} = {current}")
    return "\n".join(lines) if lines else ""


class ExperimentLoop:
    MUTABLE_TARGETS = [
        "identity/AGENT.md",
        "identity/POLICIES.yaml",
    ]

    _cycle_lock = asyncio.Lock()

    def __init__(
        self,
        agent: Any,
        data_dir: str | Path = "data/evolution/experiments",
        *,
        project_root: Path | None = None,
    ) -> None:
        self._agent = agent
        self._brain = getattr(agent, "brain", None)
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._backups_dir = self._data_dir / "backups"
        self._backups_dir.mkdir(parents=True, exist_ok=True)
        self._quality_eval = None
        try:
            from .conversation_quality import ConversationQualityEvaluator

            self._quality_eval = ConversationQualityEvaluator(
                agent, data_dir=str(self._data_dir / "quality_scores")
            )
        except Exception:
            self._quality_eval = None

        if project_root is not None:
            self._project_root = project_root
        else:
            try:
                from ..config import settings

                self._project_root = settings.project_root
            except (ImportError, AttributeError):
                self._project_root = Path(".")

        self._cleanup_old_backups()

    def _get_config(self, key: str, default: Any) -> Any:
        try:
            from ..config import settings

            v = getattr(settings, key, default)
            if v is None or (isinstance(v, (int, float)) and v < 0):
                return default
            return v
        except Exception:
            return default

    async def run_cycle(self, benchmark_report: Any = None) -> list[ExperimentResult]:
        async with ExperimentLoop._cycle_lock:
            return await self._run_cycle_locked(benchmark_report)

    async def _run_cycle_locked(self, benchmark_report: Any = None) -> list[ExperimentResult]:
        from .benchmark import BenchmarkEngine

        engine = BenchmarkEngine()
        if benchmark_report is None:
            benchmark_report = await engine.run_suite(self._agent)

        cycle_sid = datetime.now().strftime("%Y%m%d_%H%M%S")

        baseline_metrics = {
            "success_rate": benchmark_report.metrics.success_rate,
            "avg_tokens": benchmark_report.metrics.avg_tokens,
            "avg_time": benchmark_report.metrics.avg_time,
            "efficiency_score": benchmark_report.metrics.efficiency_score,
        }
        # 锚定基线: 本 cycle 的初始指标, 防止滚动漂移
        anchor_metrics = dict(baseline_metrics)

        max_experiments = self._get_config("experiments_per_cycle", _DEFAULT_MAX_EXPERIMENTS)
        quality_weight = self._load_quality_weight()
        quality_delta = 0.0
        avg_quality = 0.0
        if self._quality_eval is not None:
            try:
                avg_quality = self._quality_eval.load_weekly_average(min_samples=1)
                if avg_quality is not None:
                    quality_delta = avg_quality - 0.5
            except Exception:
                pass

        results: list[ExperimentResult] = []
        for _ in range(max_experiments):
            hypothesis = await self._generate_hypothesis(baseline_metrics, results)
            if not hypothesis:
                break
            result = await self._run_experiment(
                hypothesis, engine, baseline_metrics,
                quality_weight=quality_weight, quality_delta=quality_delta,
                anchor_metrics=anchor_metrics,
            )
            results.append(result)
            if result.action == "keep":
                baseline_metrics = result.new_metrics
            if result.action in ("keep", "discard") and result.quality_score is not None and self._quality_eval is not None:
                try:
                    self._quality_eval.save_score(result.quality_score, cycle_sid)
                except Exception:
                    pass

        if self._quality_eval is not None and results:
            try:
                self._write_feedback(results, cycle_sid)
                new_qw = self._quality_eval.adjust_quality_weight(quality_weight)
                self._save_quality_weight(new_qw)
                if abs(new_qw - quality_weight) > 0.001:
                    logger.info("[ExperimentLoop] 质量权重自适应: %.3f→%.3f", quality_weight, new_qw)
            except Exception:
                pass

        self._save_cycle(results)

        # 全局回归围栏: 本轮有 keep 的实验后, 重新 benchmark 对比原始基线
        kept = [r for r in results if r.action == "keep"]
        # 过滤: 跳过 needs_restart 的 env 实验 (重启后才生效, re-benchmark 测不到)
        active_kept = []
        for r in kept:
            if r.hypothesis and r.hypothesis.target.startswith("env:"):
                param = r.hypothesis.target[4:]
                try:
                    from openakita.config import EVOLVABLE_ENV_PARAMS
                    if EVOLVABLE_ENV_PARAMS.get(param, (None, None, None, False))[3]:
                        continue  # needs_restart, 跳过
                except Exception:
                    pass
            active_kept.append(r)

        if active_kept:
            try:
                from openakita.config import parse_bool, settings
                if parse_bool(getattr(settings, "regression_guard_enabled", True), default=True):
                    final_report = await engine.run_suite(self._agent)
                    current_sr = final_report.metrics.success_rate
                    anchor_sr = self._load_original_baseline_sr()
                    tolerance = self._get_config("max_regression_tolerance", _MAX_REGRESSION_TOLERANCE)

                    if anchor_sr is not None and current_sr < anchor_sr - tolerance:
                        logger.warning(
                            "[ExperimentLoop] 全局回归检测: current=%.3f < anchor=%.3f - %.2f, 回滚本轮实验",
                            current_sr, anchor_sr, tolerance,
                        )
                        # 跟踪每个目标只回滚一次 (取首个 kept 实验的原始内容)
                        rolled_back: set[str] = set()
                        for r in kept:
                            if not r.hypothesis:
                                continue
                            target = r.hypothesis.target
                            if target in rolled_back:
                                continue
                            rolled_back.add(target)
                            if target.startswith("env:") and r.original_content:
                                from .env_tuner import EnvTuner
                                param = target[4:]
                                tuner = EnvTuner(settings.project_root / ".env")
                                tuner.apply(param, r.original_content)
                                try:
                                    settings.reload()
                                except Exception:
                                    pass
                            elif r.original_content:
                                p = self._project_root / target
                                p.write_text(r.original_content, encoding="utf-8")
                            r.action = "reverted"
                            r.reason = f"全局回归: sr={current_sr:.3f} < anchor={anchor_sr:.3f}"
                        self._save_cycle(results)
            except Exception as e:
                logger.debug("[ExperimentLoop] 回归围栏检查跳过: %s", e)

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
            lines = []
            for r in prior_results:
                desc = r.hypothesis.description if r.hypothesis else "?"
                target = r.hypothesis.target if r.hypothesis else "?"
                line = f"- [{target}] {desc}: {r.action}"
                if r.reason:
                    line += f" (原因: {r.reason})"
                if r.delta:
                    delta_str = ", ".join(f"{k}={v:+.3f}" for k, v in r.delta.items())
                    line += f" [Δ: {delta_str}]"
                lines.append(line)
            prior_summary = "\n".join(lines)

        targets_content = {}
        for target in self.MUTABLE_TARGETS:
            p = self._project_root / target
            if p.exists():
                try:
                    targets_content[target] = await asyncio.to_thread(
                        lambda fp=p: fp.read_text(encoding="utf-8")[:2000]
                    )
                except Exception as exc:
                    logger.debug("[ExperimentLoop] 读取目标文件失败 %s: %s", target, exc)

        if not targets_content:
            return None

        targets_display = "\n\n".join(
            f"### {name}\n```\n{content}\n```" for name, content in targets_content.items()
        )

        pattern_hint = self._load_pattern_hint()

        prior_section = ""
        if prior_summary:
            prior_section = "已尝试的实验（请避免重复失败方向）:\n" + prior_summary

        memory_hint = await _get_memory_tuning_hint()
        env_targets = _get_env_targets_display()

        prompt = f"""你是一个 AI 系统优化研究员。当前系统性能指标:
- 成功率: {current_metrics.get("success_rate", 0):.1%}
- 平均 token: {current_metrics.get("avg_tokens", 0):.0f}
- 平均耗时: {current_metrics.get("avg_time", 0):.1f}s
- 效率分: {current_metrics.get("efficiency_score", 0):.1f}

{prior_section}
{memory_hint}
{pattern_hint}
可修改的目标文件及当前内容:
{targets_display}

可调参数 (env: 前缀):
{env_targets}

请提出一个具体的改进假设。输出 JSON:
{{
    "target": "目标文件路径（从上面的文件选择）或 env:参数名",
    "description": "改进描述（一句话）",
    "rationale": "为什么这个改动会提升性能",
    "proposed_change": "具体的修改内容（完整替换片段，对 env: 目标直接写数值）",
    "original_fragment": "被替换的原始片段（对 env: 目标写当前参数值）"
}}

如果没有好的改进思路，返回 {{"skip": true}}
"""
        llm_timeout = self._get_config("experiment_llm_timeout", _DEFAULT_LLM_TIMEOUT)
        try:
            response = await asyncio.wait_for(self._brain.think(prompt), timeout=llm_timeout)
            data = _parse_llm_json(response.content)
            if data.get("skip"):
                return None
            return Hypothesis(
                target=data["target"],
                description=data["description"],
                original_content=data.get("original_fragment", ""),
                proposed_content=data.get("proposed_change", ""),
                rationale=data["rationale"],
            )
        except TimeoutError:
            logger.warning("[ExperimentLoop] LLM 假设生成超时 (%ds)", llm_timeout)
            return None
        except Exception as e:
            logger.warning("[ExperimentLoop] 假设生成失败: %s", e)
            return None

    async def _run_experiment(
        self,
        hypothesis: Hypothesis,
        engine: Any,
        baseline_metrics: dict[str, float],
        quality_weight: float = 0.0,
        quality_delta: float = 0.0,
        *,
        anchor_metrics: dict[str, float] | None = None,
    ) -> ExperimentResult:
        if hypothesis.target not in self.MUTABLE_TARGETS:
            if not hypothesis.target.startswith("env:"):
                return ExperimentResult(
                    action="error", hypothesis=hypothesis, reason="目标不在允许列表中"
                )
            return await self._run_env_experiment(
                hypothesis, engine, baseline_metrics,
                quality_weight=quality_weight, quality_delta=quality_delta,
                anchor_metrics=anchor_metrics,
            )

        target_path = (self._project_root / hypothesis.target).resolve()
        if not target_path.is_relative_to(self._project_root.resolve()):
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

        if not hypothesis.original_content:
            return ExperimentResult(action="error", hypothesis=hypothesis, reason="原始片段为空")
        if not hypothesis.proposed_content:
            return ExperimentResult(action="error", hypothesis=hypothesis, reason="替换内容为空")

        new_content, match_err = self._fuzzy_match_and_replace(
            original_full, hypothesis.original_content, hypothesis.proposed_content
        )
        if new_content is None:
            return ExperimentResult(action="error", hypothesis=hypothesis, reason=match_err)

        valid, syntax_err = self._validate_syntax(target_path, new_content)
        if not valid:
            logger.warning("[ExperimentLoop] 语法验证失败: %s", syntax_err)
            return ExperimentResult(
                action="error", hypothesis=hypothesis, reason=f"语法验证失败: {syntax_err}"
            )

        backup_path = self._backups_dir / f"backup_{target_path.name}_{int(time.time())}"
        backup_path.write_text(original_full, encoding="utf-8")

        try:
            target_path.write_text(new_content, encoding="utf-8")

            report = await engine.run_suite(self._agent)
            new_metrics = {
                "success_rate": report.metrics.success_rate,
                "avg_tokens": report.metrics.avg_tokens,
                "avg_time": report.metrics.avg_time,
                "efficiency_score": report.metrics.efficiency_score,
            }

            threshold = self._get_config(
                "experiment_improvement_threshold", _DEFAULT_IMPROVEMENT_THRESHOLD
            )
            quality_score = self._compute_quality_score(
                report, baseline_metrics=baseline_metrics, new_metrics=new_metrics
            )
            if self._is_improvement(
                baseline_metrics, new_metrics, threshold,
                quality_delta=quality_delta, quality_weight=quality_weight,
                anchor_metrics=anchor_metrics,
            ):
                logger.info("[ExperimentLoop] ✓ 保留改进: %s", hypothesis.description)
                return ExperimentResult(
                    action="keep",
                    hypothesis=hypothesis,
                    baseline_metrics=baseline_metrics,
                    new_metrics=new_metrics,
                    delta={k: new_metrics[k] - baseline_metrics[k] for k in baseline_metrics},
                    quality_score=quality_score,
                    original_content=original_full,
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
                    quality_score=quality_score,
                )
        except asyncio.CancelledError:
            target_path.write_text(original_full, encoding="utf-8")
            raise
        except Exception as e:
            target_path.write_text(original_full, encoding="utf-8")
            return ExperimentResult(action="error", hypothesis=hypothesis, reason=str(e))
        finally:
            if backup_path.exists():
                backup_path.unlink(missing_ok=True)

    async def _run_env_experiment(
        self, hypothesis: Hypothesis, engine: Any, baseline_metrics: dict[str, float],
        quality_weight: float = 0.0, quality_delta: float = 0.0,
        *, anchor_metrics: dict[str, float] | None = None,
    ) -> ExperimentResult:
        """处理 env:PARAM 类型的目标 — 修改 .env 文件中的参数"""
        param = hypothesis.target[4:]
        try:
            from openakita.config import EVOLVABLE_ENV_PARAMS, settings
        except ImportError:
            return ExperimentResult(
                action="error", hypothesis=hypothesis, reason="无法加载EVOLVABLE_ENV_PARAMS"
            )

        if param not in EVOLVABLE_ENV_PARAMS:
            return ExperimentResult(
                action="error", hypothesis=hypothesis, reason="参数不在白名单中"
            )

        _, min_val, max_val, needs_restart = EVOLVABLE_ENV_PARAMS[param]
        try:
            import re as _re

            raw = str(hypothesis.proposed_content).strip()
            m = _re.search(r"[-+]?\d*\.?\d+", raw)
            if not m:
                return ExperimentResult(action="error", hypothesis=hypothesis, reason=f"无法解析数值: {raw[:40]}")
            num_val = float(m.group())
        except ValueError:
            return ExperimentResult(action="error", hypothesis=hypothesis, reason="非数字值")

        if num_val < min_val or num_val > max_val:
            return ExperimentResult(
                action="error",
                hypothesis=hypothesis,
                reason=f"值 {num_val} 超出范围 [{min_val}, {max_val}]",
            )

        value_str = str(int(num_val)) if num_val == int(num_val) else str(num_val)
        # 保存当前值作回滚参考 (从 settings 读取, 精确可靠)
        orig_env_val = str(getattr(settings, param.lower(), EVOLVABLE_ENV_PARAMS[param][0]))

        from .env_tuner import EnvTuner

        tuner = EnvTuner(settings.project_root / ".env")
        tuner.cleanup_backups()
        backup, ok = tuner.apply(param, value_str)
        if not ok:
            return ExperimentResult(action="error", hypothesis=hypothesis, reason=".env 写入失败")

        if not needs_restart:
            changed = settings.reload()
            logger.info("[EnvTuner] 热重载: %s (changed=%s)", param, changed)

        try:
            report = await engine.run_suite(self._agent)
            new_metrics = {
                "success_rate": report.metrics.success_rate,
                "avg_tokens": report.metrics.avg_tokens,
                "avg_time": report.metrics.avg_time,
                "efficiency_score": report.metrics.efficiency_score,
            }
            threshold = self._get_config(
                "experiment_improvement_threshold", _DEFAULT_IMPROVEMENT_THRESHOLD
            )
            quality_score = self._compute_quality_score(
                report, baseline_metrics=baseline_metrics, new_metrics=new_metrics
            )
            if self._is_improvement(
                baseline_metrics, new_metrics, threshold,
                quality_delta=quality_delta, quality_weight=quality_weight,
                anchor_metrics=anchor_metrics,
            ):
                logger.info("[EnvTuner] ✓ 保留 env:%s=%s", param, num_val)
                if needs_restart:
                    from openakita import config
                    config._restart_requested = True
                    logger.info("[EnvTuner] 已请求重启以应用 env:%s", param)
                return ExperimentResult(
                    action="keep",
                    hypothesis=hypothesis,
                    baseline_metrics=baseline_metrics,
                    new_metrics=new_metrics,
                    delta={k: new_metrics[k] - baseline_metrics[k] for k in baseline_metrics},
                    quality_score=quality_score,
                    original_content=orig_env_val,  # settings 读取的精确值, 用于回归回滚
                )
            else:
                tuner.rollback(backup)
                if not needs_restart:
                    settings.reload()
                logger.info("[EnvTuner] ✗ 回滚 env:%s", param)
                return ExperimentResult(
                    action="discard",
                    hypothesis=hypothesis,
                    baseline_metrics=baseline_metrics,
                    new_metrics=new_metrics,
                    delta={k: new_metrics[k] - baseline_metrics[k] for k in baseline_metrics},
                    reason=f"指标未改善{' (需重启生效)' if needs_restart else ''}",
                    quality_score=quality_score,
                )
        except asyncio.CancelledError:
            tuner.rollback(backup)
            if not needs_restart:
                settings.reload()
            raise
        except Exception as e:
            tuner.rollback(backup)
            if not needs_restart:
                settings.reload()
            return ExperimentResult(action="error", hypothesis=hypothesis, reason=str(e))

    @staticmethod
    def _fuzzy_match_and_replace(
        original_full: str, fragment: str, replacement: str
    ) -> tuple[str | None, str]:
        if fragment in original_full:
            if fragment.endswith("\n") and not replacement.endswith("\n"):
                replacement += "\n"
            return original_full.replace(fragment, replacement, 1), ""

        normalized_full = re.sub(r"\s+", " ", original_full)
        normalized_frag = re.sub(r"\s+", " ", fragment)
        if normalized_frag in normalized_full:
            lines_full = original_full.splitlines(keepends=True)
            lines_frag = fragment.splitlines(keepends=True)
            best_ratio = 0.0
            best_start = -1
            frag_len = len(lines_frag)
            for i in range(len(lines_full) - frag_len + 1):
                candidate = lines_full[i : i + frag_len]
                ratio = SequenceMatcher(None, "".join(candidate), fragment).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_start = i

            if best_ratio >= _FUZZY_MATCH_THRESHOLD and best_start >= 0:
                matched = "".join(lines_full[best_start : best_start + frag_len])
                if matched.endswith("\n") and not replacement.endswith("\n"):
                    replacement += "\n"
                new_lines = lines_full[:best_start] + [replacement]
                if best_start + frag_len < len(lines_full):
                    new_lines += lines_full[best_start + frag_len :]
                result = "".join(new_lines)
                if result != original_full:
                    logger.info("[ExperimentLoop] 空白归一化匹配成功 (ratio=%.2f)", best_ratio)
                    return result, ""

        lines_full = original_full.splitlines(keepends=True)
        lines_frag = fragment.splitlines(keepends=True)
        frag_len = len(lines_frag)
        if frag_len == 0:
            return None, "替换片段为空"

        best_ratio = 0.0
        best_start = -1
        for i in range(len(lines_full) - frag_len + 1):
            candidate = lines_full[i : i + frag_len]
            ratio = SequenceMatcher(None, "".join(candidate), fragment).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = i

        if best_ratio >= _FUZZY_MATCH_THRESHOLD and best_start >= 0:
            matched = "".join(lines_full[best_start : best_start + frag_len])
            if matched.endswith("\n") and not replacement.endswith("\n"):
                replacement += "\n"
            new_lines = lines_full[:best_start] + [replacement]
            if best_start + frag_len < len(lines_full):
                new_lines += lines_full[best_start + frag_len :]
            result = "".join(new_lines)
            if result != original_full:
                logger.info(
                    "[ExperimentLoop] 模糊匹配成功 (ratio=%.2f, line=%d)",
                    best_ratio,
                    best_start,
                )
                return result, ""

        return (
            None,
            f"无法匹配替换片段 (最佳相似度={best_ratio:.2f}, 阈值={_FUZZY_MATCH_THRESHOLD})",
        )

    @staticmethod
    def _validate_syntax(filepath: Path, content: str) -> tuple[bool, str]:
        suffix = filepath.suffix.lower()
        if suffix == ".py":
            try:
                import ast

                ast.parse(content)
                return True, ""
            except SyntaxError as e:
                return False, f"Python 语法错误 (line {e.lineno}): {e.msg}"
        elif suffix in (".yaml", ".yml"):
            try:
                import yaml

                yaml.safe_load(content)
                return True, ""
            except Exception as e:
                return False, f"YAML 格式错误: {e}"
        return True, ""

    @staticmethod
    def _is_improvement(
        old: dict[str, float],
        new: dict[str, float],
        threshold: float = _DEFAULT_IMPROVEMENT_THRESHOLD,
        quality_delta: float = 0.0,
        quality_weight: float = 0.0,
        *,
        anchor_metrics: dict[str, float] | None = None,
    ) -> bool:
        sr_old = old.get("success_rate", 0)
        sr_new = new.get("success_rate", 0)

        # 锚定检查: 不能比原始基线差太多 (防止滚动漂移)
        if anchor_metrics is not None:
            anchor_sr = anchor_metrics.get("success_rate", 0)
            if sr_new < anchor_sr - _MAX_REGRESSION_TOLERANCE:
                return False

        # 成功率天花板容错: 已接近满分时允许小幅波动
        # 避免因 100% 基线导致任何实验都无法被判定为"改善"
        if sr_old >= 0.95:
            if sr_new < 0.85:
                return False
        elif sr_new < sr_old:
            return False

        tok_old = old.get("avg_tokens", 1)
        tok_new = new.get("avg_tokens", 1)
        time_old = old.get("avg_time", 1)
        time_new = new.get("avg_time", 1)

        w = max(0.0, 1.0 - quality_weight)
        score_delta = (
            w * 0.5 * (sr_new - sr_old)
            + w * 0.3 * (tok_old - tok_new) / max(tok_old, 1)
            + w * 0.2 * (time_old - time_new) / max(time_old, 1)
            + quality_weight * quality_delta
        )
        return score_delta > threshold

    @staticmethod
    def _compute_quality_score(
        report: Any,
        baseline_metrics: dict[str, float] | None = None,
        new_metrics: dict[str, float] | None = None,
    ) -> Any:
        try:
            from .conversation_quality import QualityScore

            s = QualityScore()
            metric = getattr(report, "metrics", None)
            if metric is not None:
                s.relevance = min(1.0, max(0.0, metric.success_rate))
                cat_scores = getattr(metric, "category_scores", {})
                if cat_scores and len(cat_scores) > 0:
                    vals = list(cat_scores.values())
                    s.correctness = round(sum(vals) / len(vals), 3)
                    s.completeness = round(1.0 - (max(vals) - min(vals)) / max(max(vals), 0.01), 3)
                else:
                    s.correctness = min(1.0, max(0.0, metric.success_rate))
                    s.completeness = 0.5

                # 混合效率分: 全局水平(0.5) + 实验改善幅度(0.5)
                global_eff = 0.5 * (1.0 - min(1.0, metric.avg_time / 600.0)) + 0.5 * (
                    1.0 - min(1.0, metric.avg_tokens / 10000.0)
                )
                delta_eff = 0.5
                if baseline_metrics and new_metrics:
                    tok_old = baseline_metrics.get("avg_tokens", 1)
                    tok_new = new_metrics.get("avg_tokens", 1)
                    time_old = baseline_metrics.get("avg_time", 1)
                    time_new = new_metrics.get("avg_time", 1)
                    tok_improve = max(-1.0, min(1.0, (tok_old - tok_new) / max(tok_old, 1)))
                    time_improve = max(-1.0, min(1.0, (time_old - time_new) / max(time_old, 1)))
                    delta_eff = 0.5 + 0.25 * tok_improve + 0.25 * time_improve
                s.efficiency = round(0.5 * global_eff + 0.5 * delta_eff, 3)
                s.compute_overall()
            return s
        except Exception:
            return None

    def _cleanup_old_backups(self, max_age_days: int = _BACKUP_MAX_AGE_DAYS) -> None:
        cutoff = time.time() - max_age_days * 86400
        for f in self._backups_dir.glob("backup_*"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    logger.debug("[ExperimentLoop] 清理过期备份: %s", f.name)
            except Exception:
                pass

    def _load_original_baseline_sr(self) -> float | None:
        try:
            from openakita.config import settings
            path = settings.data_dir / "evolution" / "benchmarks" / "original_baseline.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                return float(data.get("metrics", {}).get("success_rate", 0))
        except Exception:
            pass
        return None

    def _load_quality_weight(self) -> float:
        try:
            path = self._data_dir / "quality_weight.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                w = data.get("weight", 0.10)
                if isinstance(w, (int, float)) and 0.0 <= w <= 0.30:
                    return float(w)
        except Exception:
            pass
        # 热启动: 初次运行时从合理值开始 (0.13 而非 0.10)
        warm = self._get_config("quality_weight_in_improvement", 0.10)
        return max(warm, 0.13) if warm <= 0.10 else warm

    def _save_quality_weight(self, weight: float) -> None:
        try:
            path = self._data_dir / "quality_weight.json"
            path.write_text(
                json.dumps({"weight": round(weight, 3), "ts": time.time()}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _write_feedback(self, results: list[ExperimentResult], cycle_sid: str = "") -> None:
        try:
            from openakita.config import settings

            fb_path = settings.data_dir / "evolution" / "feedback.json"
            fb_path.parent.mkdir(parents=True, exist_ok=True)
            existing: list[dict] = []
            if fb_path.exists():
                try:
                    existing = json.loads(fb_path.read_text(encoding="utf-8"))
                    if not isinstance(existing, list):
                        existing = []
                except Exception:
                    existing = []
            for r in results:
                if r.action in ("keep", "discard"):
                    existing.append({
                        "session_id": cycle_sid,
                        "rating": "good" if r.action == "keep" else "bad",
                        "description": r.hypothesis.description if r.hypothesis else "",
                    })
            if len(existing) > 100:
                existing = existing[-100:]
            fb_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _load_pattern_hint() -> str:
        try:
            from ..config import settings

            patterns_path = settings.data_dir / "evolution" / "patterns" / "effective_patterns.json"
            if not patterns_path.exists():
                return ""
            import json as _json

            data = _json.loads(patterns_path.read_text(encoding="utf-8"))
            if not isinstance(data, list) or len(data) == 0:
                return ""
            lines = []
            for p in data[:3]:
                tools = p.get("sequence", p.get("tools", []))
                freq = p.get("frequency", p.get("count", "?"))
                if isinstance(tools, list) and tools:
                    lines.append(f"- {' → '.join(str(t) for t in tools[:5])} (频率: {freq})")
            if lines:
                return "\n已学习的高效工具链模式 (可参考用于优化工具组合):\n" + "\n".join(lines)
        except Exception:
            pass
        return ""

    def _save_cycle(self, results: list[ExperimentResult]) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._data_dir / f"{ts}_cycle.json"
        data = [
            {
                "action": r.action,
                "description": r.hypothesis.description if r.hypothesis else "",
                "delta": r.delta,
                "reason": r.reason,
                "quality_score": (
                    {
                        "overall": r.quality_score.overall,
                        "relevance": r.quality_score.relevance,
                        "correctness": getattr(r.quality_score, "correctness", 0),
                        "completeness": getattr(r.quality_score, "completeness", 0),
                        "efficiency": r.quality_score.efficiency,
                    }
                    if r.quality_score is not None
                    else None
                ),
            }
            for r in results
        ]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
