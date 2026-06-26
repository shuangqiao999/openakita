"""Phase 3: Agent Factory — deep persona generation from graph + LanceDB retrieval."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable

from .models import DeductionAgentProfile
from .store import DeductionGraphStore
from .preprocessor import DeductionPreprocessor

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

## 全书关键片段（LanceDB 语义检索）
{context}

## 高频共现关键词标签
{keywords}

## 输出 JSON — 必须是纯 JSON 对象
{{
  "persona": "详细的人格描述 (80-150字), 包括性格特征、价值观、行为模式、人物弧光演变",
  "background": "背景故事 (80-150字), 包括关键经历、社会关系、动机、性格变迁",
  "goals": ["目标1", "目标2", "目标3"]
}}

【重要】只返回纯JSON对象。不要```json代码块。不要任何解释文字。"""

_PERSONA_PROMPT_FALLBACK = """基于以下实体信息和原文背景，为该人物生成一个独立人格档案。返回 JSON。

## 实体信息
- 名称: {name}
- 类型: {type}
- 描述: {description}

## 原文背景
{context}

## 输出 JSON — 必须是纯 JSON 对象
{{
  "persona": "详细的人格描述 (50-100字), 包括性格特征、价值观、行为模式",
  "background": "背景故事 (50-100字), 包括关键经历、社会关系、动机",
  "goals": ["目标1", "目标2"]
}}

【重要】只返回纯JSON对象。不要```json代码块。不要任何解释文字。"""


async def create_agents_from_graph(
    graph: DeductionGraphStore,
    source_material: str,
    log_fn: Callable[[str, str], None],
    preprocessor: DeductionPreprocessor | None = None,
) -> list[DeductionAgentProfile]:
    from openakita.llm.client import LLMClient
    from openakita.config import settings

    persons = graph.get_entities_by_type("Person")
    if not persons:
        result = graph._conn.execute(
            f"MATCH (e:{graph.NODE_TABLE}) RETURN e.id, e.name, e.type, e.description"
        )
        persons = []
        while result.has_next():
            r = result.get_next()
            persons.append({"id": r[0], "name": r[1], "type": r[2], "description": r[3]})

    max_agents = min(len(persons), settings.deduction_max_agents)
    log_fn("agents", f"从 {len(persons)} 个实体中生成最多 {max_agents} 个智能体")

    client = LLMClient()
    agents: list[DeductionAgentProfile] = []

    for i, person in enumerate(persons[:max_agents]):
        person_name = person.get("name", f"Agent-{i}")

        # ── 深度人格模式: LanceDB 全文检索 ──
        if preprocessor and preprocessor.result:
            fragments = preprocessor.retrieve_for_entity(
                person_name, top_k=10,
                must_contain={person_name},
            )
            if fragments:
                from openakita.core.tokenizer import compress_to_keywords
                full_context = "\n---\n".join(fragments)
                keywords = compress_to_keywords(full_context, top_k=10)
                prompt = _PERSONA_PROMPT.format(
                    name=person_name,
                    type=person.get("type", "Person"),
                    description=person.get("description", ""),
                    context=full_context[:8000],
                    keywords=", ".join(keywords) if keywords else "无",
                )
            else:
                prompt = _PERSONA_PROMPT_FALLBACK.format(
                    name=person_name,
                    type=person.get("type", "Person"),
                    description=person.get("description", ""),
                    context=source_material[:2000],
                )
        else:
            prompt = _PERSONA_PROMPT_FALLBACK.format(
                name=person_name,
                type=person.get("type", "Person"),
                description=person.get("description", ""),
                context=source_material[:2000],
            )

        system = "You are a JSON-only character profile generator. Output ONLY a valid JSON object. NO markdown, NO explanations."
        messages = [{"role": "user", "content": prompt}]

        try:
            response = await client.chat(messages, system=system, temperature=0.7)
            content = _extract_text(response)
            profile_data = _parse_persona_json(content)
        except Exception as e:
            logger.warning("[Deduction] Agent persona gen failed for %s: %s", person_name, e)
            profile_data = {
                "persona": f"{person_name}是一个参与事件的独立个体",
                "background": "来自原文背景",
                "goals": ["参与互动", "表达观点"],
            }

        agent_profile = DeductionAgentProfile(
            entity_id=person.get("id", uuid.uuid4().hex[:8]),
            name=person_name,
            persona=profile_data.get("persona", ""),
            background=profile_data.get("background", ""),
            goals=profile_data.get("goals", []),
        )
        agents.append(agent_profile)

        # Store in Kuzu
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

        log_fn("agents", f"  [{i+1}/{max_agents}] {person_name}: {agent_profile.persona[:80]}...")

    return agents


def _parse_persona_json(raw: str) -> dict[str, Any]:
    import re as _re
    data = _try_extract_json(raw)
    if not isinstance(data, dict):
        # LLM returned array — take first element
        if isinstance(data, list) and data and isinstance(data[0], dict):
            data = data[0]
        else:
            return {}
    return {
        "persona": data.get("persona", ""),
        "background": data.get("background", ""),
        "goals": data.get("goals", []),
    }


def _try_extract_json(raw: str):
    raw = raw.strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    cleaned = re.sub(r'```(?:json)?\s*\n?', '', raw)
    cleaned = re.sub(r'\n?```', '', cleaned).strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass
    for pat in (r'\{[\s\S]*\}', r'\[[\s\S]*\]'):
        m = re.search(pat, cleaned)
        if m:
            try:
                return json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                continue
    if raw.strip().startswith('"'):
        try:
            return json.loads("{" + raw.strip() + "}")
        except (json.JSONDecodeError, ValueError):
            pass
    return {} if raw.startswith("{") else []
