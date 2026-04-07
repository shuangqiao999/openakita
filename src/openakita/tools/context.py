"""
工具上下文 - 依赖注入

解耦 Handler 与 Agent，提供统一的依赖注入接口。
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..tools.catalog import ToolCatalog
    from ..tools.mcp import MCPClient
    from ..tools.mcp_catalog import MCPCatalog


@dataclass
class ToolContext:
    """
    工具执行上下文 - 依赖注入容器

    使用示例:
        # 创建上下文
        context = ToolContext(
            mcp_catalog=mcp_catalog,
            mcp_client=mcp_client,
            tool_catalog=tool_catalog,
        )

        # 注入Handler
        handler = BrowserHandler(context)

        # Handler 通过 context 访问所需服务
        # 而非直接访问 agent.xxx
    """

    mcp_catalog: Optional["MCPCatalog"] = None
    mcp_client: Optional["MCPClient"] = None
    tool_catalog: Optional["ToolCatalog"] = None

    # 额外的运行时上下文
    session_id: str | None = None
    user_id: str | None = None

    # 扩展字段（用于传递额外的依赖）
    extra: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """获取上下文中的值"""
        if hasattr(self, key):
            return getattr(self, key)
        return self.extra.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设置上下文中的值"""
        if hasattr(self, key):
            setattr(self, key, value)
        else:
            self.extra[key] = value


@dataclass
class BrowserContext:
    """浏览器专用上下文"""

    manager: Any = None  # BrowserManager
    tools: Any = None  # PlaywrightTools


@dataclass
class FilesystemContext:
    """文件系统专用上下文"""

    root_path: str | None = None
    allowed_paths: list[str] = field(default_factory=list)


# 便捷函数：从旧方式迁移
def create_context_from_agent(agent: Any) -> ToolContext:
    """
    从旧版Agent对象创建ToolContext（迁移兼容）

    逐步淘汰：最终所有Handler应直接接收ToolContext
    """
    context = ToolContext(
        session_id=getattr(agent, "session_id", None),
        user_id=getattr(agent, "user_id", None),
    )

    # 迁移旧属性
    if hasattr(agent, "mcp_catalog"):
        context.mcp_catalog = agent.mcp_catalog
    if hasattr(agent, "mcp_client"):
        context.mcp_client = agent.mcp_client
    if hasattr(agent, "tool_catalog"):
        context.tool_catalog = agent.tool_catalog

    return context
