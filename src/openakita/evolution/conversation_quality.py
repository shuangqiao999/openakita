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

from ._utils import strip_json

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
            data = json.loads(strip_json(response.content))
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

    def load_weekly_average(self, min_samples: int = 10) -> float | None:
        cutoff = datetime.now().timestamp() - 7 * 86400
        scores = []
        for f in sorted(self._data_dir.glob("*.json")):
            if f.stat().st_mtime >= cutoff:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    scores.append(data.get("overall", 0.5))
                except Exception:
                    continue
        if len(scores) < min_samples:
            return None
        return sum(scores) / len(scores)

    def adjust_quality_weight(self, current_weight: float) -> float:
        from openakita.config import settings

        feedback_path = settings.data_dir / "evolution" / "feedback.json"
        if not feedback_path.exists():
            return self._adjust_by_quality_trend(current_weight)

        try:
            data = json.loads(feedback_path.read_text(encoding="utf-8"))
            if not isinstance(data, list) or len(data) < 5:
                return self._adjust_by_quality_trend(current_weight)

            matches = 0
            valid = 0
            for fb in data:
                sid = fb.get("session_id", "")
                candidates = list(self._data_dir.glob(f"*_{sid[:8]}.json"))
                if not candidates:
                    continue
                score_data = json.loads(candidates[0].read_text(encoding="utf-8"))
                eval_score = score_data.get("overall", 0.5)
                user_liked = fb.get("rating") == "good"
                if (eval_score >= 0.7 and user_liked) or (eval_score < 0.4 and not user_liked):
                    matches += 1
                valid += 1

            if valid < 3:
                return self._adjust_by_quality_trend(current_weight)

            correlation = matches / valid
            if correlation > 0.6:
                new_weight = min(current_weight + 0.01, 0.30)
            elif correlation < 0.3:
                new_weight = max(current_weight - 0.01, 0.05)
            else:
                return current_weight

            if abs(new_weight - current_weight) > 0.001:
                logger.info(
                    "[QualityEval] 权重调整: %.2f→%.2f (匹配率=%.2f)",
                    current_weight, new_weight, correlation,
                )
            return new_weight
        except Exception:
            return current_weight

    def _adjust_by_quality_trend(self, current_weight: float) -> float:
        try:
            avg = self.load_weekly_average(min_samples=2)
            if avg is None:
                return current_weight

            if avg > 0.55:
                new_weight = min(current_weight + 0.01, 0.25)
            elif avg < 0.45:
                new_weight = max(current_weight - 0.01, 0.05)
            else:
                return current_weight

            if abs(new_weight - current_weight) > 0.001:
                logger.info(
                    "[QualityEval] 趋势自调: %.2f→%.2f (质量均值=%.3f)",
                    current_weight, new_weight, avg,
                )
            return new_weight
        except Exception:
            return current_weight

    def should_sample(self) -> bool:
        return random.random() < self._sample_rate
