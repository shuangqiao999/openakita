"""
Multi-Agent 研究组织协调器

多个专职 Agent 协作驱动系统进化:
- Analyst: 分析性能数据，识别瓶颈
- Prompt Engineer: 提出 prompt 改进方案
- Tool Developer: 发现并创建缺失工具/技能
- Safety Auditor: 审查所有修改的安全性

改进:
- P0-1: 性能数据采集（failures + tool_stats）
- P0-2: 采纳阈值复用 ExperimentLoop._is_improvement
- P0-3: Tool Developer 用 LLM 生成技能规范
- P0-4: 模糊匹配替换复用 ExperimentLoop._fuzzy_match_and_replace
- P1-5: Analyst prompt 增加示例格式
- P1-6: LLM 超时控制
- P1-7: asyncio.Lock 并发锁
- P1-8: 配置化参数
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _parse_llm_json(text: str) -> Any:
    from . import strip_json_fences

    return json.loads(strip_json_fences(text))


_DEFAULT_LLM_TIMEOUT = 60
_DEFAULT_IMPROVEMENT_THRESHOLD = 0.05
_DEFAULT_MAX_PROPOSALS = 2
_DEFAULT_MAX_BENCHMARKS = 2


@dataclass
class ResearchProposal:
    agent_role: str
    description: str
    target: str
    content: str
    risk_level: str = "low"


@dataclass
class AuditVerdict:
    proposal_id: int
    approved: bool
    reason: str = ""
    risk_level: str = "low"


@dataclass
class ResearchCycleResult:
    timestamp: str = ""
    proposals_count: int = 0
    approved_count: int = 0
    adopted_count: int = 0
    queued_count: int = 0
    rejected_reasons: list[str] = field(default_factory=list)
    improvements: list[dict] = field(default_factory=list)
    queued_for_approval: list[dict] = field(default_factory=list)


ANALYST_PROMPT = """你是 AI 系统性能分析师。分析以下数据并识别最多 3 个最大的改进机会:

性能指标: {metrics}
最近失败模式: {failures}
工具使用统计: {tool_stats}

严格按以下 JSON 数组格式输出（不要解释）:
[{{"opportunity": "具体描述", "priority": 1-10, "category": "prompt/tool/memory/strategy"}}]

示例:
[{{"opportunity": "工具选择经常在 web_search 和 fetch_bookmarked 之间犹豫导致多余调用", "priority": 8, "category": "prompt"}}]

如果数据不足无法分析，返回空数组 []
"""

PROMPT_ENGINEER_PROMPT = """你是 prompt 优化工程师。针对以下改进机会提出具体的 prompt 修改方案:

机会: {opportunity}
当前相关 prompt 片段: {current_prompt}

输出 JSON:
{{"section": "文件路径", "original": "原文（尽量精确匹配）", "proposed": "修改后", "hypothesis": "理由"}}
如果不适合修改 prompt，返回 {{"skip": true, "reason": "..."}}
"""

TOOL_DEVELOPER_PROMPT = """你是工具开发专家。针对以下改进机会，设计一个具体工具/技能的规范:

机会: {opportunity}

输出 JSON:
{{"name": "工具名（英文snake_case）", "description": "功能描述", "use_case": "使用场景"}}
如果不需要新工具，返回 {{"skip": true, "reason": "..."}}
"""

SAFETY_AUDITOR_PROMPT = """你是安全审计员。审查以下拟议修改:

修改方案: {proposal}

请检查:
1. 是否会破坏核心逻辑
2. 是否引入安全漏洞
3. 是否可能导致性能严重退化
4. 是否修改了不应修改的部分

严格按以下 JSON 格式输出:
{{"approved": true, "reason": "安全评估理由", "risk_level": "low"}}
或
{{"approved": false, "reason": "拒绝原因", "risk_level": "high"}}
"""


class ResearchOrg:
    ALLOWED_SECTIONS = frozenset({"identity/AGENT.md", "identity/POLICIES.yaml"})

    _cycle_lock: asyncio.Lock | None = None

    def __init__(
        self,
        agent: Any,
        data_dir: str | Path = "data/evolution/research",
        *,
        project_root: Path | None = None,
    ) -> None:
        self._agent = agent
        self._brain = getattr(agent, "brain", None)
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

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

    async def run_research_cycle(self, performance_data: dict | None = None) -> ResearchCycleResult:
        if ResearchOrg._cycle_lock is None:
            ResearchOrg._cycle_lock = asyncio.Lock()

        async with ResearchOrg._cycle_lock:
            return await self._run_cycle_locked(performance_data)

    async def _run_cycle_locked(self, performance_data: dict | None = None) -> ResearchCycleResult:
        if not self._brain:
            return ResearchCycleResult(timestamp=datetime.now().isoformat())

        if performance_data is None:
            performance_data = self._gather_performance_data()

        if performance_data.get("metrics", {}).get("success_rate") is None:
            logger.info("[ResearchOrg] 性能数据不足，跳过研究周期")
            return ResearchCycleResult(timestamp=datetime.now().isoformat())

        timeout = self._get_config("research_llm_timeout", _DEFAULT_LLM_TIMEOUT)
        opportunities = await self._run_analyst(performance_data, timeout)
        if not opportunities:
            return ResearchCycleResult(timestamp=datetime.now().isoformat())

        proposals = await self._run_engineers(opportunities, timeout)
        if not proposals:
            return ResearchCycleResult(timestamp=datetime.now().isoformat(), proposals_count=0)

        verdicts = await self._run_auditor(proposals, timeout)
        max_proposals = self._get_config("research_max_proposals", _DEFAULT_MAX_PROPOSALS)
        approved_pairs = [(p, v) for p, v in zip(proposals, verdicts, strict=False) if v.approved][
            :max_proposals
        ]

        adopted = []
        queued = []
        rejected_reasons = [v.reason for v in verdicts if not v.approved]

        max_benchmarks = self._get_config("research_max_benchmarks", _DEFAULT_MAX_BENCHMARKS)
        benchmark_count = 0
        for _i, (proposal, verdict) in enumerate(approved_pairs):
            if verdict.risk_level == "high" and proposal.agent_role == "prompt_engineer":
                req_id = self._submit_to_approval_queue(proposal, verdict, performance_data)
                queued.append(
                    {
                        "role": proposal.agent_role,
                        "description": proposal.description,
                        "approval_id": req_id,
                    }
                )
                continue
            if benchmark_count >= max_benchmarks:
                break
            success, new_metrics = await self._apply_and_verify(proposal, performance_data)
            benchmark_count += 1
            if success:
                adopted.append({"role": proposal.agent_role, "description": proposal.description})
                if new_metrics:
                    performance_data["metrics"] = new_metrics

        result = ResearchCycleResult(
            timestamp=datetime.now().isoformat(),
            proposals_count=len(proposals),
            approved_count=len(approved_pairs),
            adopted_count=len(adopted),
            queued_count=len(queued),
            rejected_reasons=rejected_reasons,
            improvements=adopted,
            queued_for_approval=queued,
        )
        self._save_cycle_result(result)
        return result

    async def _run_analyst(
        self, performance_data: dict, timeout: int = _DEFAULT_LLM_TIMEOUT
    ) -> list[dict]:
        prompt = ANALYST_PROMPT.format(
            metrics=json.dumps(performance_data.get("metrics", {}), ensure_ascii=False),
            failures=json.dumps(performance_data.get("failures", [])[:5], ensure_ascii=False),
            tool_stats=json.dumps(performance_data.get("tool_stats", {}), ensure_ascii=False),
        )
        try:
            response = await asyncio.wait_for(self._brain.chat_simple(prompt), timeout=timeout)
            result = _parse_llm_json(response)
            if not isinstance(result, list):
                logger.warning("[ResearchOrg] Analyst 返回非数组格式")
                return []
            return result
        except TimeoutError:
            logger.warning("[ResearchOrg] Analyst LLM 超时 (%ds)", timeout)
            return []
        except Exception as e:
            logger.warning("[ResearchOrg] Analyst 分析失败: %s", e)
            return []

    async def _run_engineers(
        self, opportunities: list[dict], timeout: int = _DEFAULT_LLM_TIMEOUT
    ) -> list[ResearchProposal]:
        proposals = []
        for opp in opportunities[:3]:
            category = opp.get("category", "")
            if category == "prompt":
                proposal = await self._engineer_prompt(opp, timeout)
                if proposal:
                    proposals.append(proposal)
            elif category == "tool":
                proposal = await self._engineer_tool(opp, timeout)
                if proposal:
                    proposals.append(proposal)
            else:
                logger.debug("[ResearchOrg] 未处理类别: %s", category)
        return proposals

    async def _engineer_prompt(self, opp: dict, timeout: int) -> ResearchProposal | None:
        current_prompt = ""
        agent_md = self._project_root / "identity" / "AGENT.md"
        if agent_md.exists():
            current_prompt = agent_md.read_text(encoding="utf-8")[:2000]
        prompt = PROMPT_ENGINEER_PROMPT.format(
            opportunity=json.dumps(opp, ensure_ascii=False),
            current_prompt=current_prompt,
        )
        try:
            response = await asyncio.wait_for(self._brain.chat_simple(prompt), timeout=timeout)
            data = _parse_llm_json(response)
            if data.get("skip"):
                return None
            return ResearchProposal(
                agent_role="prompt_engineer",
                description=opp.get("opportunity", ""),
                target=data.get("section", "identity/AGENT.md"),
                content=json.dumps(data, ensure_ascii=False),
            )
        except TimeoutError:
            logger.warning("[ResearchOrg] Prompt Engineer 超时")
            return None
        except Exception as e:
            logger.debug("[ResearchOrg] Prompt Engineer 失败: %s", e)
            return None

    async def _engineer_tool(self, opp: dict, timeout: int) -> ResearchProposal | None:
        prompt = TOOL_DEVELOPER_PROMPT.format(opportunity=json.dumps(opp, ensure_ascii=False))
        try:
            response = await asyncio.wait_for(self._brain.chat_simple(prompt), timeout=timeout)
            data = _parse_llm_json(response)
            if data.get("skip"):
                return None
            return ResearchProposal(
                agent_role="tool_developer",
                description=data.get("description", opp.get("opportunity", "")),
                target="skills/",
                content=json.dumps(data, ensure_ascii=False),
            )
        except TimeoutError:
            logger.warning("[ResearchOrg] Tool Developer 超时")
            return None
        except Exception as e:
            logger.debug("[ResearchOrg] Tool Developer 失败: %s", e)
            return None

    async def _run_auditor(
        self, proposals: list[ResearchProposal], timeout: int = _DEFAULT_LLM_TIMEOUT
    ) -> list[AuditVerdict]:
        verdicts = []
        for i, proposal in enumerate(proposals):
            prompt = SAFETY_AUDITOR_PROMPT.format(
                proposal=json.dumps(
                    {
                        "role": proposal.agent_role,
                        "description": proposal.description,
                        "target": proposal.target,
                        "content": proposal.content[:1500],
                    },
                    ensure_ascii=False,
                )
            )
            try:
                response = await asyncio.wait_for(self._brain.chat_simple(prompt), timeout=timeout)
                data = _parse_llm_json(response)
                verdicts.append(
                    AuditVerdict(
                        proposal_id=i,
                        approved=data.get("approved", False),
                        reason=data.get("reason", ""),
                        risk_level=data.get("risk_level", "low"),
                    )
                )
            except TimeoutError:
                verdicts.append(AuditVerdict(proposal_id=i, approved=False, reason="审计超时"))
            except Exception:
                verdicts.append(AuditVerdict(proposal_id=i, approved=False, reason="审计异常"))
        return verdicts

    async def _apply_and_verify(
        self, proposal: ResearchProposal, performance_data: dict | None = None
    ) -> tuple[bool, dict | None]:
        if proposal.agent_role == "prompt_engineer":
            return await self._apply_prompt_change(proposal, performance_data)
        elif proposal.agent_role == "tool_developer":
            return await self._generate_skill(proposal), None
        return False, None

    async def _apply_prompt_change(
        self, proposal: ResearchProposal, performance_data: dict | None = None
    ) -> tuple[bool, dict | None]:
        from .experiment_loop import ExperimentLoop

        full = None
        target = None
        try:
            data = json.loads(proposal.content)
            original = data.get("original", "")
            proposed = data.get("proposed", "")
            section = data.get("section", "")
            if not original or not proposed or not section:
                return False, None

            if section not in self.ALLOWED_SECTIONS:
                logger.warning("[ResearchOrg] 非法目标: %s", section)
                return False, None

            target = (self._project_root / section).resolve()
            if not target.is_relative_to(self._project_root.resolve()):
                return False, None
            if not target.exists():
                return False, None

            full = target.read_text(encoding="utf-8")

            touched_ratio = len(original) / max(len(full), 1)
            if touched_ratio > 0.3:
                return False, None
            if len(proposed) < 10:
                return False, None

            new_content, match_err = ExperimentLoop._fuzzy_match_and_replace(
                full, original, proposed
            )
            if new_content is None:
                logger.warning("[ResearchOrg] 匹配失败: %s", match_err)
                return False, None

            valid, syntax_err = ExperimentLoop._validate_syntax(target, new_content)
            if not valid:
                logger.warning("[ResearchOrg] 语法验证失败: %s", syntax_err)
                return False, None

            if target.suffix.lower() == ".md":
                from .prompt_optimizer import PromptOptimizer

                tpl_ok, tpl_err = PromptOptimizer._validate_template_vars(new_content)
                if not tpl_ok:
                    return False, None

            target.write_text(new_content, encoding="utf-8")
            from .benchmark import BenchmarkEngine

            engine = BenchmarkEngine()
            report = await engine.run_suite(self._agent)

            baseline_metrics = dict((performance_data or {}).get("metrics", {}))
            baseline_metrics.setdefault("avg_time", 0)
            new_metrics = {
                "success_rate": report.metrics.success_rate,
                "avg_tokens": report.metrics.avg_tokens,
                "avg_time": report.metrics.avg_time,
                "efficiency_score": report.metrics.efficiency_score,
            }
            threshold = self._get_config(
                "research_improvement_threshold", _DEFAULT_IMPROVEMENT_THRESHOLD
            )
            if ExperimentLoop._is_improvement(baseline_metrics, new_metrics, threshold):
                logger.info("[ResearchOrg] ✓ Prompt 变更已采纳")
                return True, new_metrics

            target.write_text(full, encoding="utf-8")
            return False, None
        except asyncio.CancelledError:
            if full is not None and target is not None:
                target.write_text(full, encoding="utf-8")
            raise
        except Exception as e:
            if full is not None and target is not None:
                target.write_text(full, encoding="utf-8")
            logger.warning("[ResearchOrg] Prompt 应用失败: %s", e)
            return False, None

    async def _generate_skill(self, proposal: ResearchProposal) -> bool:
        skill_gen = getattr(self._agent, "skill_generator", None)
        if not skill_gen:
            return False
        try:
            desc = proposal.description
            try:
                spec = json.loads(proposal.content)
                desc = spec.get("description", desc)
            except Exception:
                pass
            result = await skill_gen.generate(desc)
            success = bool(result and getattr(result, "success", False))
            if success:
                logger.info(
                    "[ResearchOrg] ✓ 技能生成: %s",
                    getattr(result, "skill_name", "unknown"),
                )
            return success
        except Exception as e:
            logger.warning("[ResearchOrg] 技能生成失败: %s", e)
            return False

    def _submit_to_approval_queue(
        self,
        proposal: ResearchProposal,
        verdict: AuditVerdict,
        performance_data: dict | None = None,
    ) -> str:
        from .approval_queue import ApprovalQueue, ApprovalRequest

        queue = ApprovalQueue()
        original = ""
        proposed = ""
        target_file = proposal.target
        if proposal.agent_role == "prompt_engineer":
            try:
                data = json.loads(proposal.content)
                original = data.get("original", "")
                proposed = data.get("proposed", "")
                target_file = data.get("section", proposal.target)
            except Exception:
                pass

        req = ApprovalRequest(
            source="research_org",
            agent_role=proposal.agent_role,
            risk_level=verdict.risk_level,
            title=proposal.description[:100],
            description=proposal.description,
            target_file=target_file,
            original_content=original,
            proposed_content=proposed,
            hypothesis=verdict.reason,
            metrics_before=(performance_data or {}).get("metrics", {}),
        )
        return queue.submit(req)

    def _gather_performance_data(self) -> dict:
        metrics = {}
        failures: list[dict] = []
        tool_stats: dict[str, int] = {}

        try:
            from .benchmark import BenchmarkEngine

            engine = BenchmarkEngine()
            results_dir = engine._results_dir
            result_files = sorted(results_dir.glob("*.json"), reverse=True)
            if result_files:
                data = json.loads(result_files[0].read_text(encoding="utf-8"))
                metrics = data.get("metrics", {})
            else:
                baseline = engine._load_latest_baseline()
                if baseline:
                    metrics = {
                        "success_rate": baseline.success_rate,
                        "avg_tokens": baseline.avg_tokens,
                        "efficiency_score": baseline.efficiency_score,
                    }
        except Exception:
            pass

        try:
            from ..config import settings

            fa_dir = settings.data_dir / "failure_analysis"
            if fa_dir.is_dir():
                for date_dir in sorted(fa_dir.iterdir(), reverse=True)[:3]:
                    if not date_dir.is_dir():
                        continue
                    for f in sorted(date_dir.glob("*.json"), reverse=True)[:5]:
                        try:
                            d = json.loads(f.read_text(encoding="utf-8"))
                            failures.append(
                                {
                                    "root_cause": d.get("root_cause", "unknown"),
                                    "harness_gap": d.get("harness_gap", ""),
                                    "suggestion": str(d.get("suggestion", ""))[:100],
                                }
                            )
                        except Exception:
                            continue
        except Exception:
            pass

        try:
            from ..config import settings

            traces_dir = settings.data_dir / "react_traces"
            if traces_dir.is_dir():
                from .pattern_learner import PatternLearner

                all_tools = PatternLearner._extract_tool_names(
                    self._collect_recent_tool_calls(traces_dir)
                )
                freq: dict[str, int] = defaultdict(int)
                for t in all_tools:
                    freq[t] += 1
                tool_stats = dict(sorted(freq.items(), key=lambda x: -x[1])[:10])
        except Exception:
            pass

        logger.info(
            "[ResearchOrg] 数据采集: metrics=%s, failures=%d条, tools=%d种",
            bool(metrics),
            len(failures),
            len(tool_stats),
        )
        return {
            "metrics": metrics,
            "failures": failures[:5],
            "tool_stats": tool_stats,
        }

    def _collect_recent_tool_calls(self, traces_dir: Path) -> list[dict]:
        steps = []
        trace_files: list[Path] = []
        for date_dir in traces_dir.iterdir():
            if date_dir.is_dir():
                trace_files.extend(date_dir.glob("*.json"))
        trace_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        for f in trace_files[:30]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                steps.extend(data.get("iterations", data.get("steps", [])))
            except Exception:
                continue
        return steps

    def _save_cycle_result(self, result: ResearchCycleResult) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._data_dir / f"{ts}_research_cycle.json"
        data = {
            "timestamp": result.timestamp,
            "proposals_count": result.proposals_count,
            "approved_count": result.approved_count,
            "adopted_count": result.adopted_count,
            "queued_count": result.queued_count,
            "rejected_reasons": result.rejected_reasons,
            "improvements": result.improvements,
            "queued_for_approval": result.queued_for_approval,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
