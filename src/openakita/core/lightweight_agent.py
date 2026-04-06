"""
轻量级 Agent - 整合快速回复、工具路由、智能缓存
"""

import logging
from typing import Any

from .quick_reply import QuickReplyHandler
from .tool_cache import SmartToolCache
from .tool_router import ToolRouter, ToolExecutor

logger = logging.getLogger(__name__)


class LightweightAgent:
    """
    轻量级智能响应系统
    - 简单对话立即回复（0 Token）
    - 工具调用智能路由
    - 语义缓存加速
    """

    def __init__(self, llm_client=None):
        self.llm = llm_client
        self.quick_reply = QuickReplyHandler()
        self.tool_router = ToolRouter()
        self.tool_executor = ToolExecutor()
        self.tool_cache = SmartToolCache(similarity_threshold=0.6)

        self._stats = {"quick_replies": 0, "tool_calls": 0, "llm_calls": 0, "cache_hits": 0}

    def process(self, user_input: str, session_id: str | None = None) -> dict[str, Any]:
        """
        处理用户消息
        返回: {
            "type": "reply" | "tool_call" | "llm",
            "content": str,
            "tool_name": str,
            "tool_params": dict,
            "from_cache": bool
        }
        """

        cached = self.tool_cache.get(user_input, session_id)
        if cached:
            tool_name, params = cached
            self._stats["cache_hits"] += 1
            self._stats["tool_calls"] += 1
            logger.info(f"[LightweightAgent] Cache hit: {tool_name}")
            return {
                "type": "tool_call",
                "tool_name": tool_name,
                "tool_params": params,
                "from_cache": True,
                "content": None,
            }

        result = self.quick_reply.handle(user_input)
        if result:
            response, _ = result
            self._stats["quick_replies"] += 1
            logger.info(f"[LightweightAgent] Quick reply: {response[:50]}...")
            return {
                "type": "reply",
                "content": response,
                "tool_name": None,
                "tool_params": {},
                "from_cache": False,
            }

        route_result = self.tool_router.route(user_input)
        if route_result:
            tool_name, params = route_result
            self._stats["tool_calls"] += 1
            self.tool_cache.set(user_input, tool_name, params, session_id)
            logger.info(f"[LightweightAgent] Routed to tool: {tool_name}, params={params}")
            return {
                "type": "tool_call",
                "tool_name": tool_name,
                "tool_params": params,
                "from_cache": False,
                "content": None,
            }

        self._stats["llm_calls"] += 1
        logger.info(f"[LightweightAgent] Fallback to LLM: {user_input[:50]}...")

        return {
            "type": "llm",
            "content": None,
            "tool_name": None,
            "tool_params": {},
            "from_cache": False,
            "raw_input": user_input,
        }

    async def execute(self, tool_name: str, params: dict[str, Any] | None = None) -> str:
        """
        执行已路由的工具

        Args:
            tool_name: 工具名称
            params: 参数字典

        Returns:
            执行结果字符串
        """
        if params is None:
            params = {}

        return await self.tool_executor.execute(tool_name, params)

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        cache_stats = self.tool_cache.get_stats()
        return {
            "quick_replies": self._stats["quick_replies"],
            "tool_calls": self._stats["tool_calls"],
            "llm_calls": self._stats["llm_calls"],
            "cache_hits": self._stats["cache_hits"],
            "cache": cache_stats,
            "total_processed": sum(self._stats.values()),
        }

    def reset_stats(self):
        """重置统计"""
        self._stats = {"quick_replies": 0, "tool_calls": 0, "llm_calls": 0, "cache_hits": 0}
