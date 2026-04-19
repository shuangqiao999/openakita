"""
用户档案处理器

处理用户档案相关的系统技能：
- update_user_profile: 更新档案
- skip_profile_question: 跳过问题
- get_user_profile: 获取档案
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class ProfileHandler:
    """用户档案处理器"""

    TOOLS = [
        "update_user_profile",
        "skip_profile_question",
        "get_user_profile",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        if tool_name == "update_user_profile":
            return self._update_profile(params)
        elif tool_name == "skip_profile_question":
            return self._skip_question(params)
        elif tool_name == "get_user_profile":
            return self._get_profile(params)
        else:
            return f"❌ Unknown profile tool: {tool_name}"

    def _update_profile(self, params: dict) -> str:
        """更新用户档案。

        - 已知 key 直接落档
        - 未知 key 自动 fallback 到 add_memory，作为 fact 存入语义记忆，
          避免小白用户/非程序员场景因白名单卡死（旧版直接报错的 P0 问题）
        """
        available_keys = self.agent.profile_manager.get_available_keys()

        if "key" not in params:
            updated: list[str] = []
            saved_as_memory: list[str] = []
            for k, v in params.items():
                if k in available_keys:
                    self.agent.profile_manager.update_profile(k, str(v))
                    updated.append(f"{k} = {v}")
                else:
                    if self._save_unknown_as_memory(k, v):
                        saved_as_memory.append(f"{k} = {v}")
            parts: list[str] = []
            if updated:
                parts.append(f"✅ 已更新档案: {', '.join(updated)}")
            if saved_as_memory:
                parts.append(
                    f"📝 以下信息不在档案白名单内，已作为长期记忆保存: "
                    f"{', '.join(saved_as_memory)}"
                )
            if parts:
                return "\n".join(parts)
            return (
                f"❌ 参数格式错误，正确用法: {{\"key\": \"name\", \"value\": \"小明\"}}\n"
                f"可用的键: {', '.join(available_keys)}"
            )

        key = params["key"]
        value = params.get("value", "")

        if key not in available_keys:
            if self._save_unknown_as_memory(key, value):
                return (
                    f"📝 档案白名单不含 `{key}`，已作为长期记忆保存: {key} = {value}\n"
                    f"（如需正式建档请联系管理员扩展 USER_PROFILE_ITEMS）"
                )
            return f"❌ 未知的档案项: {key}\n可用的键: {', '.join(available_keys)}"

        self.agent.profile_manager.update_profile(key, value)
        return f"✅ 已更新档案: {key} = {value}"

    def _save_unknown_as_memory(self, key: str, value: Any) -> bool:
        """把白名单外的 key=value 当作 fact 落入语义记忆。

        失败返回 False，由调用方决定是否报错。
        """
        try:
            mm = getattr(self.agent, "memory_manager", None)
            if mm is None or not hasattr(mm, "add_memory"):
                return False
            from ...memory.types import Memory, MemoryPriority, MemoryType

            content = f"用户档案补充: {key} = {value}"
            mem = Memory(
                content=content,
                type=MemoryType.FACT,
                priority=MemoryPriority.LONG_TERM,
                source="profile_fallback",
                importance_score=0.7,
                tags=["profile_extra", key],
            )
            mm.add_memory(mem)
            return True
        except Exception as e:
            logger.warning(f"[ProfileHandler] fallback to memory failed: {e}")
            return False

    def _skip_question(self, params: dict) -> str:
        """跳过档案问题"""
        key = params["key"]
        self.agent.profile_manager.skip_question(key)
        return f"✅ 已跳过问题: {key}"

    def _get_profile(self, params: dict) -> str:
        """获取用户档案"""
        summary = self.agent.profile_manager.get_profile_summary()

        if not summary:
            return "用户档案为空\n\n提示: 通过对话中分享信息来建立档案"

        return summary


def create_handler(agent: "Agent"):
    """创建用户档案处理器"""
    handler = ProfileHandler(agent)
    return handler.handle
