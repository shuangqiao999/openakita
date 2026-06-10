"""
工具链路模式学习

从历史成功任务中提取高效工具调用模式，编码为 best practices 注入 prompt。
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.7
MAX_PATTERNS = 10


@dataclass
class ToolPattern:
    category: str
    pattern: str
    confidence: float
    evidence_count: int
    avg_tokens: float = 0.0
    created_at: str = ""


@dataclass
class ToolSequence:
    task_category: str
    tools: list[str]
    tokens_used: int
    success: bool
    time_seconds: float = 0.0


class PatternLearner:
    def __init__(self, agent: Any, data_dir: str | Path = "data/evolution/patterns") -> None:
        self._agent = agent
        self._brain = getattr(agent, "brain", None)
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._patterns_file = self._data_dir / "effective_patterns.json"

    async def learn_from_history(self, days: int = 7) -> list[ToolPattern]:
        sequences = self._extract_sequences(days)
        if len(sequences) < 5:
            logger.info("[PatternLearner] 历史数据不足(%d条), 跳过学习", len(sequences))
            return []

        clusters = self._cluster_by_category(sequences)
        efficient_clusters = self._find_efficient_clusters(clusters)

        patterns = []
        for cat, seqs in efficient_clusters.items():
            pattern = await self._summarize_pattern(cat, seqs)
            if pattern and pattern.confidence >= CONFIDENCE_THRESHOLD:
                patterns.append(pattern)

        self._save_patterns(patterns)
        return patterns

    def _extract_sequences(self, days: int) -> list[ToolSequence]:
        from ..config import settings

        trace_dir = settings.data_dir / "react_traces"
        if not trace_dir.exists():
            return []

        sequences = []
        cutoff = datetime.now().timestamp() - days * 86400
        trace_files = []
        for date_dir in trace_dir.iterdir():
            if date_dir.is_dir():
                trace_files.extend(date_dir.glob("*.json"))
        trace_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        for trace_file in trace_files[:200]:
            try:
                if trace_file.stat().st_mtime < cutoff:
                    break
                data = json.loads(trace_file.read_text(encoding="utf-8"))
                result = data.get("result", "")
                is_success = result == "success" or data.get("success", False)
                if not is_success:
                    continue
                tools = []
                for step in data.get("iterations", data.get("steps", [])):
                    tool_name = step.get("tool_name") or step.get("tool", "")
                    if tool_name:
                        tools.append(tool_name)
                    tool_calls = step.get("tool_calls", [])
                    for tc in tool_calls:
                        tn = tc.get("name", "") or tc.get("tool_name", "")
                        if tn:
                            tools.append(tn)
                if tools:
                    sequences.append(
                        ToolSequence(
                            task_category=data.get("category", "general"),
                            tools=tools,
                            tokens_used=data.get("total_tokens", 0),
                            success=True,
                            time_seconds=data.get("elapsed_seconds", 0),
                        )
                    )
            except Exception:
                continue
        return sequences

    def _cluster_by_category(self, sequences: list[ToolSequence]) -> dict[str, list[ToolSequence]]:
        clusters: dict[str, list[ToolSequence]] = defaultdict(list)
        for seq in sequences:
            clusters[seq.task_category].append(seq)
        return dict(clusters)

    def _find_efficient_clusters(
        self, clusters: dict[str, list[ToolSequence]]
    ) -> dict[str, list[ToolSequence]]:
        efficient = {}
        for cat, seqs in clusters.items():
            if len(seqs) < 3:
                continue
            median_tokens = sorted(s.tokens_used for s in seqs)[len(seqs) // 2]
            efficient_seqs = [s for s in seqs if s.tokens_used < median_tokens * 0.8]
            if len(efficient_seqs) >= 2:
                efficient[cat] = efficient_seqs
        return efficient

    async def _summarize_pattern(
        self, category: str, sequences: list[ToolSequence]
    ) -> ToolPattern | None:
        if not self._brain:
            return self._rule_based_pattern(category, sequences)

        repr_seqs = [" → ".join(s.tools[:10]) for s in sequences[:5]]
        prompt = f"""以下是在"{category}"类任务中高效完成的工具调用序列:
{chr(10).join(f"- {seq}" for seq in repr_seqs)}

请总结为一条简洁的 best practice（一句话），格式:
"在[场景]时，应该[具体操作步骤]"
"""
        try:
            response = await self._brain.chat_simple(prompt)
            return ToolPattern(
                category=category,
                pattern=response.strip().strip('"'),
                confidence=min(len(sequences) / 10.0, 1.0),
                evidence_count=len(sequences),
                avg_tokens=sum(s.tokens_used for s in sequences) / len(sequences),
                created_at=datetime.now().isoformat(),
            )
        except Exception as e:
            logger.warning("[PatternLearner] 模式总结失败: %s", e)
            return self._rule_based_pattern(category, sequences)

    def _rule_based_pattern(
        self, category: str, sequences: list[ToolSequence]
    ) -> ToolPattern | None:
        tool_freq: dict[str, int] = defaultdict(int)
        for seq in sequences:
            for tool in seq.tools:
                tool_freq[tool] += 1
        top_tools = sorted(tool_freq.items(), key=lambda x: -x[1])[:5]
        if not top_tools:
            return None
        pattern = f"在{category}任务中，常用工具: {', '.join(t[0] for t in top_tools)}"
        return ToolPattern(
            category=category,
            pattern=pattern,
            confidence=0.5,
            evidence_count=len(sequences),
            avg_tokens=sum(s.tokens_used for s in sequences) / len(sequences),
            created_at=datetime.now().isoformat(),
        )

    def load_patterns(self) -> list[ToolPattern]:
        if not self._patterns_file.exists():
            return []
        try:
            data = json.loads(self._patterns_file.read_text(encoding="utf-8"))
            return [ToolPattern(**p) for p in data]
        except Exception:
            return []

    def _save_patterns(self, patterns: list[ToolPattern]) -> None:
        existing = self.load_patterns()
        existing_cats = {p.category for p in existing}
        for p in patterns:
            if p.category in existing_cats:
                existing = [e for e in existing if e.category != p.category]
            existing.append(p)
        existing = sorted(existing, key=lambda p: -p.confidence)[:MAX_PATTERNS]
        data = [
            {
                "category": p.category,
                "pattern": p.pattern,
                "confidence": p.confidence,
                "evidence_count": p.evidence_count,
                "avg_tokens": p.avg_tokens,
                "created_at": p.created_at,
            }
            for p in existing
        ]
        self._patterns_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get_injection_text(self) -> str:
        patterns = self.load_patterns()
        if not patterns:
            return ""
        lines = []
        for p in patterns[:5]:
            if p.confidence >= CONFIDENCE_THRESHOLD:
                lines.append(f"- {p.pattern}")
        return "\n".join(lines) if lines else ""
