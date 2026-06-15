"""
工具链路模式学习

从历史成功任务中提取高效工具调用模式，编码为 best practices 注入 prompt。

改进:
- P0-1: 递归提取工具名，兼容任意嵌套 trace 结构
- P0-2: 集成到 prompt builder（通过 get_injection_text）
- P1-3: 效率簇筛选用 ≤median + 时间辅助
- P1-4: LLM 总结格式化 prompt + 后处理
- P1-5: 增量学习（last_learn.json 记录进度）
- P2-6: 注入文本长度限制（默认 500 字符）
- P2-7: Jaccard 语义去重
- P2-8: trace_dir 参数解耦
"""

from __future__ import annotations

import asyncio
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
_MAX_INJECTION_CHARS = 500
_MAX_SUMMARY_LENGTH = 200


@dataclass
class ToolPattern:
    category: str
    pattern: str
    confidence: float
    evidence_count: int
    avg_tokens: float = 0.0
    created_at: str = ""
    enabled: bool = True


@dataclass
class ToolSequence:
    task_category: str
    tools: list[str]
    tokens_used: int
    success: bool
    time_seconds: float = 0.0
    file_mtime: float = 0.0


class PatternLearner:
    def __init__(
        self,
        agent: Any,
        data_dir: str | Path = "data/evolution/patterns",
        *,
        trace_dir: Path | None = None,
    ) -> None:
        self._agent = agent
        self._brain = getattr(agent, "brain", None)
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._patterns_file = self._data_dir / "effective_patterns.json"
        self._learn_state_file = self._data_dir / "last_learn.json"

        if trace_dir is not None:
            self._trace_dir = trace_dir
        else:
            try:
                from ..config import settings

                self._trace_dir = settings.data_dir / "react_traces"
            except Exception:
                self._trace_dir = Path("data/react_traces")

    async def learn_from_history(
        self, days: int = 7, *, full_relearn: bool = False
    ) -> list[ToolPattern]:
        since_mtime = 0.0 if full_relearn else self._load_learn_state()
        sequences = self._extract_sequences(days, since_mtime=since_mtime)

        if len(sequences) < 5:
            logger.info("[PatternLearner] 历史数据不足(%d条), 跳过学习", len(sequences))
            return []

        logger.info("[PatternLearner] 提取 %d 条成功任务序列", len(sequences))
        clusters = self._cluster_by_category(sequences)
        efficient_clusters = self._find_efficient_clusters(clusters)
        logger.info("[PatternLearner] 识别 %d 个高效类别簇", len(efficient_clusters))

        patterns = []
        for cat, seqs in efficient_clusters.items():
            pattern = await self._summarize_pattern(cat, seqs)
            if pattern and pattern.confidence >= CONFIDENCE_THRESHOLD:
                patterns.append(pattern)

        patterns = self._deduplicate_patterns(patterns)
        self._save_patterns(patterns)

        if sequences:
            max_mtime = max(s.file_mtime for s in sequences)
            self._save_learn_state(max_mtime)

        logger.info("[PatternLearner] 学习完成, 保存 %d 条模式", len(patterns))
        return patterns

    @staticmethod
    def _extract_tool_names(data: Any, _depth: int = 0) -> list[str]:
        if _depth > 20:
            return []
        tools: list[str] = []
        if isinstance(data, dict):
            for key in ("tool_name", "tool"):
                val = data.get(key)
                if isinstance(val, str) and val:
                    tools.append(val)
            if "name" in data and isinstance(data.get("name"), str) and data["name"]:
                if any(k in data for k in ("tool_input", "arguments", "params", "tool_call_id", "input", "id")):
                    tools.append(data["name"])
            for v in data.values():
                if isinstance(v, (dict, list)):
                    tools.extend(PatternLearner._extract_tool_names(v, _depth + 1))
        elif isinstance(data, list):
            for item in data:
                tools.extend(PatternLearner._extract_tool_names(item, _depth + 1))
        return tools

    def _extract_sequences(self, days: int, *, since_mtime: float = 0.0) -> list[ToolSequence]:
        if not self._trace_dir.exists():
            logger.debug("[PatternLearner] trace 目录不存在: %s", self._trace_dir)
            return []

        sequences = []
        cutoff = datetime.now().timestamp() - days * 86400
        trace_files: list[Path] = []
        for date_dir in self._trace_dir.iterdir():
            if date_dir.is_dir():
                trace_files.extend(date_dir.glob("*.json"))
        if not trace_files:
            trace_files = list(self._trace_dir.glob("*.json"))
        trace_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        processed = 0
        skipped_old = 0
        for trace_file in trace_files[:500]:  # 上限500，避免窗口内旧文件被忽略
            try:
                mtime = trace_file.stat().st_mtime
                if mtime < cutoff:
                    break
                if mtime <= since_mtime:
                    skipped_old += 1
                    continue
                data = json.loads(trace_file.read_text(encoding="utf-8"))
                result_val = data.get("result", "")
                is_success = result_val in ("success", "completed") or data.get("success", False)
                if not is_success:
                    continue

                raw_steps = data.get("iterations", data.get("steps", []))
                tools = self._extract_tool_names(raw_steps)
                tools = list(dict.fromkeys(tools))  # 去重保序(Python 3.7+)

                if tools:
                    sequences.append(
                        ToolSequence(
                            task_category=data.get("category", "general"),
                            tools=tools,
                            tokens_used=data.get("total_tokens", 0),
                            success=True,
                            time_seconds=data.get("elapsed_seconds", 0),
                            file_mtime=mtime,
                        )
                    )
                processed += 1
            except Exception:
                continue

        if skipped_old:
            logger.debug("[PatternLearner] 跳过 %d 条已学习的 trace", skipped_old)
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
            sorted_tokens = sorted(s.tokens_used for s in seqs)
            median_tokens = sorted_tokens[len(seqs) // 2]
            sorted_times = sorted(s.time_seconds for s in seqs)
            median_time = sorted_times[len(seqs) // 2]

            efficient_seqs = [
                s
                for s in seqs
                if s.tokens_used <= max(median_tokens, 1)
                and s.time_seconds <= max(median_time, 0.1) * 1.2
            ]
            if len(efficient_seqs) >= 2:
                efficient[cat] = efficient_seqs
        return efficient

    async def _summarize_pattern(
        self, category: str, sequences: list[ToolSequence]
    ) -> ToolPattern | None:
        if not self._brain:
            return self._rule_based_pattern(category, sequences)

        repr_seqs = [" → ".join(s.tools[:10]) for s in sequences[:5]]
        seq_text = "\n".join(f"- {seq}" for seq in repr_seqs)
        prompt = (
            f'以下是在"{category}"类任务中高效完成的工具调用序列:\n'
            f"{seq_text}\n\n"
            "请总结为一条简洁的 best practice，严格按以下格式输出（不要加引号）:\n"
            "在[场景]时，应该[步骤1]→[步骤2]→[步骤3]\n\n"
            "示例: 在修改代码文件时，应该 grep 定位 → read_file 确认 → edit_file 修改 → read_lints 检查\n\n"
            "只输出一行文本，不要解释。"
        )
        try:
            llm_timeout = 600
            try:
                from ..config import settings

                v = getattr(settings, "experiment_llm_timeout", 600)
                if v and v > 0:
                    llm_timeout = v
            except Exception:
                pass
            response = await asyncio.wait_for(self._brain.think(prompt), timeout=llm_timeout)
            if isinstance(response, str):
                text = response.strip().strip('"').strip("'")
            else:
                text = str(getattr(response, "content", "")) if response else ""
            if not text or len(text) > _MAX_SUMMARY_LENGTH:
                logger.debug(
                    "[PatternLearner] LLM 输出不合规 (len=%d), 回退规则模式",
                    len(text) if text else 0,
                )
                return self._rule_based_pattern(category, sequences)
            return ToolPattern(
                category=category,
                pattern=text,
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
        tools_str = " → ".join(t[0] for t in top_tools)
        pattern = f"在{category}任务中，常用工具链路: {tools_str}"
        return ToolPattern(
            category=category,
            pattern=pattern,
            confidence=0.5,
            evidence_count=len(sequences),
            avg_tokens=sum(s.tokens_used for s in sequences) / len(sequences),
            created_at=datetime.now().isoformat(),
        )

    @staticmethod
    def _jaccard_similarity(a: str, b: str) -> float:
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a or not words_b:
            return 0.0
        return len(words_a & words_b) / len(words_a | words_b)

    def _deduplicate_patterns(self, patterns: list[ToolPattern]) -> list[ToolPattern]:
        result: list[ToolPattern] = []
        for p in sorted(patterns, key=lambda x: -x.confidence):
            if all(self._jaccard_similarity(p.pattern, r.pattern) < 0.8 for r in result):
                result.append(p)
            else:
                logger.debug("[PatternLearner] 去重: %s", p.pattern[:60])
        return result

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
            existing_cats.add(p.category)
        existing = self._deduplicate_patterns(existing)
        existing = sorted(existing, key=lambda p: -p.confidence)[:MAX_PATTERNS]
        data = [
            {
                "category": p.category,
                "pattern": p.pattern,
                "confidence": p.confidence,
                "evidence_count": p.evidence_count,
                "avg_tokens": p.avg_tokens,
                "created_at": p.created_at,
                "enabled": getattr(p, "enabled", True),
            }
            for p in existing
        ]
        self._patterns_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get_injection_text(self, max_chars: int = _MAX_INJECTION_CHARS) -> str:
        patterns = self.load_patterns()
        if not patterns:
            return ""
        lines: list[str] = []
        total = 0
        for p in patterns:
            if p.confidence < CONFIDENCE_THRESHOLD:
                continue
            if not getattr(p, "enabled", True):
                continue
            line = f"- {p.pattern}"
            if total + len(line) + 1 > max_chars:
                logger.warning("[PatternLearner] 注入文本截断 (%d/%d 字符)", total, max_chars)
                break
            lines.append(line)
            total += len(line) + 1
        return "\n".join(lines)

    def _load_learn_state(self) -> float:
        if not self._learn_state_file.exists():
            return 0.0
        try:
            data = json.loads(self._learn_state_file.read_text(encoding="utf-8"))
            return float(data.get("last_mtime", 0))
        except Exception:
            return 0.0

    def _save_learn_state(self, last_mtime: float) -> None:
        data = {
            "last_mtime": last_mtime,
            "timestamp": datetime.now().isoformat(),
        }
        self._learn_state_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
