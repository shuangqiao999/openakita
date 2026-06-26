"""Phase 3: Agent Factory — generate deduction agents from graph Person entities."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable

from .models import DeductionAgentProfile
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

_PERSONA_PROMPT = """基于以下实体信息和原文背景，为该人物生成一个独立人格档案。返回 JSON。

## 实体信息
- 名称: {name}
- 类型: {type}
- 描述: {description}

## 原文背景
{context}

## 输出 JSON
```json
{{
  "persona": "详细的人格描述 (50-100字), 包括性格特征、价值观、行为模式",
  "background": "背景故事 (50-100字), 包括关键经历、社会关系、动机",
  "goals": ["目标1", "目标2"]
}}
```

只返回 JSON，不要解释。"""


async def create_agents_from_graph(
    graph: DeductionGraphStore,
    source_material: str,
    log_fn: Callable[[str, str], None],
) -> list[DeductionAgentProfile]:
    from openakita.llm.client import LLMClient
    from openakita.config import settings

    persons = graph.get_entities_by_type("Person")
    if not persons:
        # Fallback: try all entities
        all_entities = []
        result = graph._conn.execute(f"MATCH (e:{graph.NODE_TABLE}) RETURN e.id, e.name, e.type, e.description")
        while result.has_next():
            r = result.get_next()
            all_entities.append({"id": r[0], "name": r[1], "type": r[2], "description": r[3]})
        persons = all_entities

    max_agents = min(len(persons), settings.deduction_max_agents)
    log_fn("agents", f"从 {len(persons)} 个实体中生成最多 {max_agents} 个智能体")

    client = LLMClient()
    agents: list[DeductionAgentProfile] = []

    for i, person in enumerate(persons[:max_agents]):
        system = "你是人物档案生成专家。只输出 JSON。"
        messages = [{"role": "user", "content": _PERSONA_PROMPT.format(
            name=person.get("name", "未知"),
            type=person.get("type", "Person"),
            description=person.get("description", ""),
            context=source_material[:2000],
        )}]

        try:
            response = await client.chat(messages, system=system, temperature=0.7)
            content = _extract_text(response)
            profile_data = _parse_persona_json(content)
        except Exception as e:
            logger.warning("[Deduction] Agent persona gen failed for %s: %s",
                           person.get("name", "?"), e)
            profile_data = {
                "persona": f"{person.get('name', '未知')}是一个参与事件的独立个体",
                "background": "来自原文背景",
                "goals": ["参与互动", "表达观点"],
            }

        agent_profile = DeductionAgentProfile(
            entity_id=person.get("id", uuid.uuid4().hex[:8]),
            name=person.get("name", f"Agent-{i}"),
            persona=profile_data.get("persona", ""),
            background=profile_data.get("background", ""),
            goals=profile_data.get("goals", []),
        )
        agents.append(agent_profile)

        # Store agent node in graph
        graph.upsert_agent_node(
            agent_profile.entity_id, agent_profile.name,
            agent_profile.persona, agent_profile.background,
            json.dumps(agent_profile.goals, ensure_ascii=False),
        )
        graph._conn.execute(
            f"MATCH (e:{graph.NODE_TABLE} {{id: $eid}}), "
            f"(a:{graph.AGENT_TABLE} {{id: $aid}}) "
            "CREATE (a)-[:PARTICIPATES {role: 'embodies'}]->(e)",
            {"eid": person.get("id", ""), "aid": agent_profile.entity_id},
        )

        log_fn("agents", f"  [{i+1}/{max_agents}] {agent_profile.name}: {agent_profile.persona[:60]}...")

    return agents


def _parse_persona_json(raw: str) -> dict[str, Any]:
    import re
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
        return {
            "persona": data.get("persona", ""),
            "background": data.get("background", ""),
            "goals": data.get("goals", []),
        }
    except json.JSONDecodeError:
        return {}
