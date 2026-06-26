"""Phase 4: Parallel Simulation — multi-agent interaction in virtual world."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import uuid
from typing import Any, Callable

from .models import DeductionAgentProfile, SimulationAction, SimulationRound
from .store import DeductionGraphStore

logger = logging.getLogger(__name__)


def _extract_text(response) -> str:
    if hasattr(response, "text"):
        return response.text
    if hasattr(response, "content"):
        c = response.content
        if isinstance(c, list):
            from openakita.llm.types import TextBlock
            return "".join(b.text for b in c if isinstance(b, TextBlock))
        return str(c)
    if isinstance(response, dict):
        if "choices" in response:
            return response["choices"][0]["message"]["content"]
        return str(response)
    return str(response)

_ACTION_PROMPT = """你是一个推演模拟中的智能体。根据你的角色设定和当前世界状态，决定你的下一步行动。

## 你的角色
{persona}

## 你的背景
{background}

## 你的目标
{goals}

## 当前轮次
第 {round_number} 轮

## 近期世界事件（最近 5 个）
{recent_events}

## 输出 JSON — 选择一种行动
```json
{{
  "action": "post|reply|interact|observe",
  "target": "目标实体名或留空",
  "content": "行动内容 (30-100字)"
}}
```

只返回 JSON，不要解释。"""


class SimulationEngine:

    def __init__(
        self,
        agents: list[DeductionAgentProfile],
        graph: DeductionGraphStore,
        total_rounds: int = 10,
        log_fn: Callable[[str, str], None] | None = None,
    ) -> None:
        self.agents = agents
        self.graph = graph
        self.total_rounds = total_rounds
        self._log = log_fn or (lambda p, m: None)
        self._event_history: list[dict[str, Any]] = []
        self._max_concurrent = 10

    async def run_round(self, round_number: int) -> SimulationRound:
        from openakita.llm.client import LLMClient

        sim_round = SimulationRound(round_number=round_number)
        client = LLMClient()

        # Shuffle agents for natural interaction order
        ordered = list(self.agents)
        random.shuffle(ordered)

        # Process agents in batches of _max_concurrent
        sem = asyncio.Semaphore(self._max_concurrent)

        async def process_agent(agent: DeductionAgentProfile) -> SimulationAction | None:
            async with sem:
                return await self._agent_decide(client, agent, round_number)

        tasks = [process_agent(a) for a in ordered]
        results = await asyncio.gather(*tasks)

        for action in results:
            if action is not None:
                sim_round.actions.append(action)
                self._event_history.append({
                    "agent": action.agent_id,
                    "action": action.action_type,
                    "content": action.content,
                    "round": round_number,
                    "timestamp": action.timestamp,
                })

        # Trim event history
        if len(self._event_history) > 100:
            self._event_history = self._event_history[-100:]

        # Write round events to graph
        for action in sim_round.actions:
            event_id = f"evt-{uuid.uuid4().hex[:8]}"
            self.graph.add_event(
                event_id, action.content[:200], action.action_type,
                action.timestamp, action.agent_id,
            )
            self.graph.add_acted(action.agent_id, event_id, action.action_type, action.timestamp)

        return sim_round

    async def _agent_decide(
        self, client: Any, agent: DeductionAgentProfile, round_number: int
    ) -> SimulationAction | None:
        recent = self._event_history[-5:]
        recent_text = "\n".join(
            f"- [{e.get('round', '?')}] {e.get('agent', '?')}: {e.get('content', '')[:80]}"
            for e in recent
        ) or "无近期事件"

        system = "你是推演模拟中的角色，根据角色设定做出合理的下一步行动。只输出 JSON。"
        messages = [{"role": "user", "content": _ACTION_PROMPT.format(
            persona=agent.persona,
            background=agent.background,
            goals=", ".join(agent.goals) if agent.goals else "参与互动",
            round_number=round_number,
            recent_events=recent_text,
        )}]

        try:
            response = await client.chat(messages, system=system, temperature=0.7)
            content = _extract_text(response)
            action_data = _parse_action_json(content)
        except Exception as e:
            logger.debug("[Deduction] Agent %s decision failed: %s", agent.name, e)
            return None

        from datetime import datetime
        return SimulationAction(
            agent_id=agent.entity_id,
            action_type=action_data.get("action", "observe"),
            target_id=action_data.get("target", ""),
            content=action_data.get("content", f"{agent.name}观察着周围环境"),
            timestamp=datetime.now().isoformat(),
        )


def _parse_action_json(raw: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
