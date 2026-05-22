"""知识库工具处理器"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class KnowledgeBaseHandler:
    """search_knowledge_base / save_to_knowledge_base 工具处理器"""

    TOOLS = ["search_knowledge_base", "save_to_knowledge_base"]

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    async def handle(self, tool_name: str, params: dict) -> str:
        if tool_name == "save_to_knowledge_base":
            return await self._handle_save(params)
        if tool_name != "search_knowledge_base":
            return f"未知知识库工具: {tool_name}"
        return await self._handle_search(params)

    async def _handle_save(self, params: dict) -> str:
        title = params.get("title", "").strip()
        content = params.get("content", "").strip()
        if not title:
            return "❌ save_to_knowledge_base 缺少必要参数 'title'。"
        if not content:
            return "❌ save_to_knowledge_base 缺少必要参数 'content'。"

        kb = getattr(self.agent, "kb_manager", None)
        if kb is None:
            return "知识库功能未初始化，请确认嵌入模型已配置。"

        try:
            result = await kb.ingest_text(title, content)
            if result.get("duplicate"):
                return "⚠️ 已存在同名文档，未重复保存。"
            return f"✅ 已保存到知识库：{title}（文档 ID: {result.get('doc_id', '?')}）"
        except Exception as e:
            logger.error(f"[KnowledgeBaseHandler] 保存失败: {e}")
            return f"❌ 保存到知识库时出错: {e}"

    async def _handle_search(self, params: dict) -> str:

        query = params.get("query", "")
        try:
            top_k = min(int(params.get("top_k", 5)), 20)
        except (ValueError, TypeError):
            top_k = 5

        if not query or not query.strip():
            return "❌ search_knowledge_base 缺少必要参数 'query'。"

        kb = getattr(self.agent, "kb_manager", None)
        if kb is None:
            return "知识库功能未初始化，请确认嵌入模型已配置。"

        doc_filter = params.get("doc_filter", "") or None

        try:
            results = await kb.search(query=query.strip(), top_k=top_k, doc_filter=doc_filter)
        except Exception as e:
            logger.error(f"[KnowledgeBaseHandler] 搜索失败: {e}")
            return f"❌ 搜索知识库时出错: {e}"

        if not results:
            return "知识库中未找到相关内容。"

        lines: list[str] = []
        for i, r in enumerate(results):
            doc_name = r.get("document_name", "未知文档")
            content = r.get("content", "")
            score = r.get("score", 0)
            raw_snippet = content[:400].replace("\n", " ").strip()
            snippet = raw_snippet[:400]
            if len(raw_snippet) > 400:
                snippet = snippet[:397] + "..."
            elif len(content) > 400:
                snippet += "..."

            lines.append(f"### 结果 {i + 1} — 《{doc_name}》（相关度: {score:.0%}）\n\n{snippet}")

        return "\n\n---\n\n".join(lines)


def create_handler(agent: Any):
    handler = KnowledgeBaseHandler(agent)
    return handler.handle
