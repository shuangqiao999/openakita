"""
Multi-Agent 研究组织协调器

多个专职 Agent 协作驱动系统进化:
- Analyst: 分析性能数据，识别瓶颈
- Prompt Engineer: 提出 prompt 改进方案
- Tool Developer: 发现并创建缺失工具/技能
- Safety Auditor: 审查所有修改的安全性
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ResearchProposal:
    agent_role: str
    description: str
    target: str
    content: str
    risk_level: str = "low"  # low / medium / high


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


ANALYST_PROMPT = """你是 AI 系统性能分析师。分析以下数据并识别 3 个最大的改进机会:

性能指标: {metrics}
最近失败模式: {failures}
工具使用统计: {tool_stats}

输出 JSON 列表:
[{{"opportunity": "描述", "priority": 1-10, "category": "prompt/tool/memory/strategy"}}]
"""

PROMPT_ENGINEER_PROMPT = """你是 prompt 优化工程师。针对以下改进机会提出具体的 prompt 修改方案:

机会: {opportunity}
当前相关 prompt 片段: {current_prompt}

输出 JSON:
{{"section": "文件路径", "original": "原文", "proposed": "修改后", "hypothesis": "理由"}}
如果不适合修改 prompt，返回 {{"skip": true, "reason": "..."}}
"""

SAFETY_AUDITOR_PROMPT = """你是安全审计员。审查以下拟议修改:

修改方案: {proposal}

请检查:
1. 是否会破坏核心逻辑
2. 是否引入安全漏洞
3. 是否可能导致性能严重退化
4. 是否修改了不应修改的部分

输出 JSON:
{{"approved": true/false, "reason": "...", "risk_level": "low/medium/high"}}
"""


class ResearchOrg:
    def __init__(self, agent: Any, data_dir: str | Path = "data/evolution/research") -> None:
        self._agent = agent
        self._brain = getattr(agent, "brain", None)
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    MAX_PROPOSALS_PER_CYCLE = 2
    MAX_BENCHMARK_RUNS_PER_CYCLE = 2

    async def run_research_cycle(self, performance_data: dict | None = None) -> ResearchCycleResult:
        if not self._brain:
            return ResearchCycleResult(timestamp=datetime.now().isoformat())

        if performance_data is None:
            performance_data = self._gather_performance_data()

        opportunities = await self._run_analyst(performance_data)
        if not opportunities:
            return ResearchCycleResult(timestamp=datetime.now().isoformat())

        proposals = await self._run_engineers(opportunities)
        if not proposals:
            return ResearchCycleResult(
                timestamp=datetime.now().isoformat(),
                proposals_count=0,
            )

        verdicts = await self._run_auditor(proposals)
        approved_pairs = [(p, v) for p, v in zip(proposals, verdicts, strict=False) if v.approved][
            : self.MAX_PROPOSALS_PER_CYCLE
        ]

        adopted = []
        queued = []
        rejected_reasons = [v.reason for v in verdicts if not v.approved]

        for i, (proposal, verdict) in enumerate(approved_pairs):
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
            if i >= self.MAX_BENCHMARK_RUNS_PER_CYCLE:
                break
            success = await self._apply_and_verify(proposal, performance_data)
            if success:
                adopted.append({"role": proposal.agent_role, "description": proposal.description})

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

    async def _run_analyst(self, performance_data: dict) -> list[dict]:
        prompt = ANALYST_PROMPT.format(
            metrics=json.dumps(performance_data.get("metrics", {}), ensure_ascii=False),
            failures=json.dumps(performance_data.get("failures", [])[:5], ensure_ascii=False),
            tool_stats=json.dumps(performance_data.get("tool_stats", {}), ensure_ascii=False),
        )
        try:
            response = await self._brain.chat_simple(prompt)
            return json.loads(response)
        except Exception as e:
            logger.warning("[ResearchOrg] Analyst 分析失败: %s", e)
            return []

    async def _run_engineers(self, opportunities: list[dict]) -> list[ResearchProposal]:
        from ..config import settings

        proposals = []
        for opp in opportunities[:3]:
            category = opp.get("category", "")
            if category == "prompt":
                current_prompt = ""
                agent_md = settings.project_root / "identity" / "AGENT.md"
                if agent_md.exists():
                    current_prompt = agent_md.read_text(encoding="utf-8")[:2000]
                prompt = PROMPT_ENGINEER_PROMPT.format(
                    opportunity=json.dumps(opp, ensure_ascii=False),
                    current_prompt=current_prompt,
                )
                try:
                    response = await self._brain.chat_simple(prompt)
                    data = json.loads(response)
                    if not data.get("skip"):
                        proposals.append(
                            ResearchProposal(
                                agent_role="prompt_engineer",
                                description=opp.get("opportunity", ""),
                                target=data.get("section", "identity/AGENT.md"),
                                content=json.dumps(data, ensure_ascii=False),
                            )
                        )
                except Exception as e:
                    logger.debug("[ResearchOrg] Prompt Engineer 失败: %s", e)
            elif category == "tool":
                proposals.append(
                    ResearchProposal(
                        agent_role="tool_developer",
                        description=opp.get("opportunity", ""),
                        target="skills/",
                        content=json.dumps(opp, ensure_ascii=False),
                    )
                )
        return proposals

    async def _run_auditor(self, proposals: list[ResearchProposal]) -> list[AuditVerdict]:
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
                response = await self._brain.chat_simple(prompt)
                data = json.loads(response)
                verdicts.append(
                    AuditVerdict(
                        proposal_id=i,
                        approved=data.get("approved", False),
                        reason=data.get("reason", ""),
                        risk_level=data.get("risk_level", "low"),
                    )
                )
            except Exception:
                verdicts.append(AuditVerdict(proposal_id=i, approved=False, reason="审计异常"))
        return verdicts

    async def _apply_and_verify(
        self, proposal: ResearchProposal, performance_data: dict | None = None
    ) -> bool:
        if proposal.agent_role == "prompt_engineer":
            return await self._apply_prompt_change(proposal, performance_data)
        elif proposal.agent_role == "tool_developer":
            return await self._generate_skill(proposal)
        return False

    ALLOWED_SECTIONS = frozenset({"identity/AGENT.md", "identity/POLICIES.yaml"})
    IMPROVEMENT_THRESHOLD = 1.0

    async def _apply_prompt_change(
        self, proposal: ResearchProposal, performance_data: dict | None = None
    ) -> bool:
        full = None
        target = None
        try:
            data = json.loads(proposal.content)
            original = data.get("original", "")
            proposed = data.get("proposed", "")
            section = data.get("section", "")
            if not original or not proposed or not section:
                return False

            if section not in self.ALLOWED_SECTIONS:
                logger.warning("[ResearchOrg] 非法目标: %s", section)
                return False

            from ..config import settings
            from .benchmark import BenchmarkEngine

            target = (settings.project_root / section).resolve()
            if not target.is_relative_to(settings.project_root.resolve()):
                return False
            if not target.exists():
                return False

            full = target.read_text(encoding="utf-8")
            new_content = full.replace(original, proposed, 1)
            if new_content == full:
                return False

            target.write_text(new_content, encoding="utf-8")
            engine = BenchmarkEngine()
            report = await engine.run_suite(self._agent)

            baseline_score = (performance_data or {}).get("metrics", {}).get("efficiency_score", 0)
            if report.metrics.efficiency_score - baseline_score >= self.IMPROVEMENT_THRESHOLD:
                return True

            target.write_text(full, encoding="utf-8")
            return False
        except Exception as e:
            if full is not None and target is not None:
                target.write_text(full, encoding="utf-8")
            logger.warning("[ResearchOrg] Prompt 应用失败: %s", e)
            return False

    async def _generate_skill(self, proposal: ResearchProposal) -> bool:
        skill_gen = getattr(self._agent, "skill_generator", None)
        if not skill_gen:
            return False
        try:
            result = await skill_gen.generate(proposal.description)
            return result.success if result else False
        except Exception:
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
        from .benchmark import BenchmarkEngine

        engine = BenchmarkEngine()
        baseline = engine._load_latest_baseline()
        return {
            "metrics": {
                "success_rate": baseline.success_rate if baseline else 0,
                "avg_tokens": baseline.avg_tokens if baseline else 0,
                "efficiency_score": baseline.efficiency_score if baseline else 0,
            },
            "failures": [],
            "tool_stats": {},
        }

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
