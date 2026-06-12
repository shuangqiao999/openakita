"""
对话质量评估器

使用本地 LLM 对 Agent 回复进行多维度评分
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class QualityScore:
    relevance: float = 0.5
    correctness: float = 0.5
    completeness: float = 0.5
    efficiency: float = 0.5
    overall: float = 0.5

    def compute_overall(self) -> None:
        self.overall = round(
            0.30 * self.relevance
            + 0.30 * self.correctness
            + 0.25 * self.completeness
            + 0.15 * self.efficiency,
            3,
        )


class ConversationQualityEvaluator:
    def __init__(
        self,
        agent: Any,
        data_dir: str | Path = "data/evolution/quality_scores",
        sample_rate: float = 0.1,
    ) -> None:
        self._agent = agent
        self._brain = getattr(agent, "brain", None)
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._sample_rate = sample_rate

    async def evaluate_turn(
        self, user_msg: str, assistant_reply: str, tool_calls: list[str]
    ) -> QualityScore:
        if not self._brain:
            return QualityScore()

        prompt = f"""评估对话质量 (0-10):

用户: {user_msg[:400]}
回复: {assistant_reply[:400]}
工具: {tool_calls[:10]}

JSON:
{{"relevance": 0-10, "correctness": 0-10, "completeness": 0-10, "efficiency": 0-10, "reason": "短评"}}
"""
        try:
            response = await self._brain.think(prompt)
            data = json.loads(_strip_json(response.content))
            s = QualityScore(
                relevance=data.get("relevance", 5) / 10,
                correctness=data.get("correctness", 5) / 10,
                completeness=data.get("completeness", 5) / 10,
                efficiency=data.get("efficiency", 5) / 10,
            )
            s.compute_overall()
            return s
        except Exception as e:
            logger.warning("[QualityEval] 失败: %s, 默认值", e)
            return QualityScore()

    def save_score(self, score: QualityScore, session_id: str = "") -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._data_dir / f"{ts}_{session_id[:8]}.json"
        path.write_text(json.dumps(asdict(score), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load_weekly_average(self) -> float:
        cutoff = datetime.now().timestamp() - 7 * 86400
        scores = []
        for f in sorted(self._data_dir.glob("*.json")):
            if f.stat().st_mtime >= cutoff:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    scores.append(data.get("overall", 0.5))
                except Exception:
                    continue
        return sum(scores) / len(scores) if scores else 0.5

    def should_sample(self) -> bool:
        return random.random() < self._sample_rate


def _strip_json(text: str) -> str:
    import re

    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    text = re.sub(r"\n?```\s*$", "", text)
    sb = text.find("{")
    sq = text.find("[")
    s = min(x for x in (sb, sq) if x >= 0) if (sb >= 0 or sq >= 0) else -1
    if s > 0:
        text = text[s:]
    e = max(text.rfind("}"), text.rfind("]"))
    if 0 <= e < len(text) - 1:
        text = text[: e + 1]
    return text
