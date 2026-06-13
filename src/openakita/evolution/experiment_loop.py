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

    return json.loads(strip_json_fences(text))


_DEFAULT_IMPROVEMENT_THRESHOLD = 0.02
_DEFAULT_LLM_TIMEOUT = 600
_BACKUP_MAX_AGE_DAYS = 7
_FUZZY_MATCH_THRESHOLD = 0.85


@dataclass
class Hypothesis:
    target: str
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


def _get_memory_tuning_hint() -> str:
    try:
        from openakita.config import settings

        if not getattr(settings, "memory_retrieval_tuning_enabled", True):
            return ""
        collector = None
        try:
            from .runtime_metrics import RuntimeMetricsCollector

            collector = RuntimeMetricsCollector()
        except Exception:
            return ""

        usage_rate = getattr(settings, "memory_usage_low_threshold", 0.3)
        cooldown = getattr(settings, "memory_tuning_cooldown_hours", 24)

        import time as _time

        last_tune = collector.get_last_tuning_time() if collector else 0.0
        if _time.time() - last_tune < cooldown * 3600:
            return ""

        if not collector:
            return ""
        snap = collector.collect()
        if snap.memory_usage_rate >= usage_rate:
            return ""

        return (
            f"\n⚠ 记忆使用率: {snap.memory_usage_rate:.0%} (阈值 {usage_rate:.0%})\n"
            "建议调整记忆检索参数以提升召回率:\n"
            "- env:RETRIEVAL_TOP_K (1-20): 增大可召回更多记忆\n"
            "- env:MEMORY_SIMILARITY_THRESHOLD (0.5-0.95): 降低可放宽匹配\n"
        )
    except Exception:
        return ""


def _get_env_targets_display() -> str:
    try:
        from openakita.config import EVOLVABLE_ENV_PARAMS, settings
    except ImportError:
        return ""
    lines = []
    for key, (default_val, lo, hi, restart) in sorted(EVOLVABLE_ENV_PARAMS.items()):
        current = getattr(settings, key.lower(), default_val)
        restart_note = " (需重启)" if restart else ""
        lines.append(f"- env:{key} = {current} (范围 {lo}-{hi}){restart_note}")
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
            if v is None or (isinstance(v, (int, float)) and v <= 0):
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

        baseline_metrics = {
            "success_rate": benchmark_report.metrics.success_rate,
            "avg_tokens": benchmark_report.metrics.avg_tokens,
            "avg_time": benchmark_report.metrics.avg_time,
            "efficiency_score": benchmark_report.metrics.efficiency_score,
        }

        max_experiments = self._get_config("experiments_per_cycle", _DEFAULT_MAX_EXPERIMENTS)
        results: list[ExperimentResult] = []
        for _ in range(max_experiments):
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
            lines = []
            for r in prior_results:
                desc = r.hypothesis.description if r.hypothesis else "?"
                line = f"- {desc}: {r.action}"
                if r.reason:
                    line += f" (原因: {r.reason})"
                lines.append(line)
            prior_summary = "\n".join(lines)

        targets_content = {}
        for target in self.MUTABLE_TARGETS:
            p = self._project_root / target
            if p.exists():
                targets_content[target] = p.read_text(encoding="utf-8")[:2000]

        if not targets_content:
            return None

        targets_display = "\n\n".join(
            f"### {name}\n```\n{content}\n```" for name, content in targets_content.items()
        )

        prior_section = ""
        if prior_summary:
            prior_section = "已尝试的实验（请避免重复失败方向）:\n" + prior_summary

        memory_hint = self._get_memory_tuning_hint()
        env_targets = _get_env_targets_display()

        prompt = f"""你是一个 AI 系统优化研究员。当前系统性能指标:
- 成功率: {current_metrics.get("success_rate", 0):.1%}
- 平均 token: {current_metrics.get("avg_tokens", 0):.0f}
- 平均耗时: {current_metrics.get("avg_time", 0):.1f}s
- 效率分: {current_metrics.get("efficiency_score", 0):.1f}

{prior_section}
{memory_hint}
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
    ) -> ExperimentResult:
        if hypothesis.target not in self.MUTABLE_TARGETS:
            if not hypothesis.target.startswith("env:"):
                return ExperimentResult(
                    action="error", hypothesis=hypothesis, reason="目标不在允许列表中"
                )
            return await self._run_env_experiment(hypothesis, engine, baseline_metrics)

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
            if self._is_improvement(baseline_metrics, new_metrics, threshold):
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
        self, hypothesis: Hypothesis, engine: Any, baseline_metrics: dict[str, float]
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
            num_val = float(hypothesis.proposed_content.strip())
        except ValueError:
            return ExperimentResult(action="error", hypothesis=hypothesis, reason="非数字值")

        if num_val < min_val or num_val > max_val:
            return ExperimentResult(
                action="error",
                hypothesis=hypothesis,
                reason=f"值 {num_val} 超出范围 [{min_val}, {max_val}]",
            )

        value_str = str(int(num_val)) if num_val == int(num_val) else str(num_val)

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
            if self._is_improvement(baseline_metrics, new_metrics, threshold):
                logger.info("[EnvTuner] ✓ 保留 env:%s=%s", param, num_val)
                return ExperimentResult(
                    action="keep",
                    hypothesis=hypothesis,
                    baseline_metrics=baseline_metrics,
                    new_metrics=new_metrics,
                    delta={k: new_metrics[k] - baseline_metrics[k] for k in baseline_metrics},
                )
            else:
                tuner.rollback(backup)
                if not needs_restart:
                    settings.reload()
                logger.info("[EnvTuner] ✗ 回滚 env:%s", param)
                return ExperimentResult(
                    action="discard",
                    hypothesis=hypothesis,
                    reason=f"指标未改善{' (需重启生效)' if needs_restart else ''}",
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
    ) -> bool:
        sr_old = old.get("success_rate", 0)
        sr_new = new.get("success_rate", 0)
        if sr_new < sr_old:
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

    def _cleanup_old_backups(self, max_age_days: int = _BACKUP_MAX_AGE_DAYS) -> None:
        cutoff = time.time() - max_age_days * 86400
        for f in self._backups_dir.glob("backup_*"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    logger.debug("[ExperimentLoop] 清理过期备份: %s", f.name)
            except Exception:
                pass

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
