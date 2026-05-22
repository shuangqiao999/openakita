"""知识库工具处理器"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class KnowledgeBaseHandler:
    """search / save / status / list / delete 工具处理器"""

    TOOLS = [
        "search_knowledge_base",
        "save_to_knowledge_base",
        "get_knowledge_base_status",
        "list_knowledge_base_documents",
        "delete_knowledge_base_document",
        "repair_knowledge_base_document",
        "overwrite_knowledge_base_document",
    ]

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    async def handle(self, tool_name: str, params: dict) -> str:
        if tool_name == "save_to_knowledge_base":
            return await self._handle_save(params)
        if tool_name == "get_knowledge_base_status":
            return await self._handle_status()
        if tool_name == "list_knowledge_base_documents":
            return await self._handle_list(params)
        if tool_name == "delete_knowledge_base_document":
            return await self._handle_delete(params)
        if tool_name == "repair_knowledge_base_document":
            return await self._handle_repair(params)
        if tool_name == "overwrite_knowledge_base_document":
            return await self._handle_overwrite(params)
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

    async def _handle_status(self) -> str:
        kb = getattr(self.agent, "kb_manager", None)
        if kb is None:
            return "知识库功能未初始化。"
        try:
            stats = await kb.get_stats()
        except Exception as e:
            return f"❌ 获取状态失败: {e}"

        lines = [
            "知识库状态：",
            f" - 文档总数：{stats['total_documents']}",
            f" - 就绪：{stats['ready_documents']}",
            f" - 处理中：{stats['processing_documents']}",
            f" - 失败：{stats['failed_documents']}",
            f" - 分块总数：{stats['total_chunks']}",
        ]
        if stats["recent_documents"]:
            lines.append(" - 最近上传：")
            for doc in stats["recent_documents"]:
                lines.append(f"   · {doc['name']}（{doc['ago']}）")
        return "\n".join(lines)

    async def _handle_list(self, params: dict) -> str:
        search = params.get("search", "").strip()
        try:
            limit = min(int(params.get("limit", 10)), 30)
        except (ValueError, TypeError):
            limit = 10

        kb = getattr(self.agent, "kb_manager", None)
        if kb is None:
            return "知识库功能未初始化。"

        try:
            if search:
                docs = kb.find_document_by_name(search)
            else:
                result = await kb.list_documents(limit=limit)
                docs = result.get("documents", [])
        except Exception as e:
            return f"❌ 列出文档失败: {e}"

        if not docs:
            return "知识库中没有文档。" + ("（未匹配到）" if search else "")

        lines = [f"共 {len(docs)} 个文档："]
        for d in docs:
            status_icon = {"ready": "✅", "processing": "⏳", "failed": "❌"}.get(d.get("status", ""), "❓")
            lines.append(f"\n{status_icon} {d['name']}（{d.get('file_type', '?')}，{d.get('total_chunks', 0)} 块）")
        return "\n".join(lines)

    async def _handle_delete(self, params: dict) -> str:
        name = params.get("name", "").strip()
        if not name:
            return "❌ 请指定要删除的文档名称。"

        kb = getattr(self.agent, "kb_manager", None)
        if kb is None:
            return "知识库功能未初始化。"

        docs = kb.find_document_by_name(name)
        if not docs:
            return f"未找到名称包含「{name}」的文档。"

        target = docs[0]
        try:
            await kb.delete_document(target["id"])
            return f"✅ 已删除《{target['name']}》（{target.get('total_chunks', 0)} 个分块）"
        except Exception as e:
            return f"❌ 删除失败: {e}"

    async def _handle_repair(self, params: dict) -> str:
        name = params.get("name", "").strip()
        if not name:
            return "❌ 请指定要修复的文档名称。"

        kb = getattr(self.agent, "kb_manager", None)
        if kb is None:
            return "知识库功能未初始化。"

        docs = kb.find_document_by_name(name)
        if not docs:
            return f"未找到名称包含「{name}」的文档。"

        target = docs[0]
        try:
            result = await kb.repair_document(target["id"])
            if result.get("repaired"):
                return f"✅ 已修复《{target['name']}》（重建 {result['chunks']} 个向量）"
            return f"⚠️ 修复跳过：{result.get('reason', '未知原因')}"
        except Exception as e:
            return f"❌ 修复失败: {e}"

    async def _handle_overwrite(self, params: dict) -> str:
        name = params.get("name", "").strip()
        content = params.get("content", "").strip()
        if not name:
            return "❌ 请指定要覆盖的文档名称。"
        if not content:
            return "❌ 请提供新的文本内容。"

        kb = getattr(self.agent, "kb_manager", None)
        if kb is None:
            return "知识库功能未初始化。"

        docs = kb.find_document_by_name(name)
        if not docs:
            return f"未找到名称包含「{name}」的文档，将作为新文档保存。"

        target = docs[0]
        try:
            await kb.delete_document(target["id"])
            result = await kb.ingest_text(target["name"], content)
            return f"✅ 已覆盖更新《{target['name']}》（新文档 ID: {result.get('doc_id', '?')}）"
        except Exception as e:
            return f"❌ 覆盖失败: {e}"

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
