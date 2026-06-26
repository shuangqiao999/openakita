"""Phase 2: GraphRAG — chunk source → LLM extract triples → write Kuzu graph."""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Callable

from .models import Ontology
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

_CHUNK_SIZE = 1500

_EXTRACT_PROMPT = """从以下文本中抽取实体和关系的三元组，返回 JSON 数组。

## 实体类型（仅使用以下类型）
{entity_types}

## 关系类型（仅使用以下类型）
{relation_types}

## 输出格式
```json
[
  {{"entity": "实体名", "type": "类型", "description": "简短描述"}},
  {{"source": "实体A", "target": "实体B", "relation": "关系名", "evidence": "原文证据"}}
]
```

## 规则
1. 只使用上面列出的实体/关系类型
2. 每个三元组需要 evidence（原文证据）
3. 只返回 JSON，不要解释

## 文本
{text}"""


async def build_graph(
    source: str,
    graph: DeductionGraphStore,
    ontology: Ontology | None,
    log_fn: Callable[[str, str], None],
) -> None:
    from openakita.llm.client import LLMClient

    client = LLMClient()
    chunks = _chunk_text(source, _CHUNK_SIZE)
    log_fn("graph", f"文档分块: {len(chunks)} 块 (chunk_size={_CHUNK_SIZE})")

    entity_type_names = [e.name for e in ontology.entities] if ontology else [
        "Person", "Organization", "Event", "Concept", "Location"
    ]
    relation_type_names = [r.name for r in ontology.relations] if ontology else [
        "works_for", "involved_in", "located_in", "opposes", "supports"
    ]

    total_entities = 0
    total_relations = 0

    for i, chunk in enumerate(chunks):
        system = "你是知识图谱构建专家，从文本中精确抽取实体和关系三元组。只输出 JSON。"
        messages = [{"role": "user", "content": _EXTRACT_PROMPT.format(
            text=chunk, entity_types=", ".join(entity_type_names),
            relation_types=", ".join(relation_type_names),
        )}]

        try:
            response = await client.chat(messages, system=system, temperature=0.1)
            content = _extract_text(response)
            entities, relations = _parse_extraction(content)
        except Exception as e:
            logger.warning("[Deduction] Graph extract chunk %d failed: %s", i, e)
            log_fn("graph", f"  块 {i+1}/{len(chunks)} 抽取失败: {e}")
            continue

        for ent in entities:
            ent_id = _make_id(ent.get("entity", ""), ent.get("type", ""))
            graph.upsert_entity(
                ent_id, ent.get("entity", ""), ent.get("type", ""),
                ent.get("description", ""),
            )
            total_entities += 1

        for rel in relations:
            sid = _make_id(rel.get("source", ""), "")
            tid = _make_id(rel.get("target", ""), "")
            graph.upsert_relation(
                sid, tid, rel.get("relation", ""),
                evidence=rel.get("evidence", ""),
            )
            total_relations += 1

        log_fn("graph", f"  块 {i+1}/{len(chunks)}: {len(entities)} 实体, {len(relations)} 关系")

        # Also store chunk nodes
        chunk_id = f"chunk-{uuid.uuid4().hex[:8]}"
        graph.upsert_chunk(chunk_id, chunk[:500], "source", i)


def _chunk_text(text: str, size: int) -> list[str]:
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if len(current) + len(p) > size and current:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks or [text[:size]]


def _parse_extraction(raw: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    json_match = re.search(r"\[[\s\S]*\]", raw)
    if not json_match:
        return [], []
    try:
        data = json.loads(json_match.group(0))
    except json.JSONDecodeError:
        return [], []

    entities: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for item in data:
        if "entity" in item:
            entities.append(item)
        elif "source" in item:
            relations.append(item)
    return entities, relations


def _make_id(name: str, etype: str) -> str:
    import hashlib
    raw = f"{name}:{etype}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()[:12]
