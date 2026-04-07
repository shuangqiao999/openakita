"""
工具上下文 - 依赖注入

解耦 Handler 与 Agent，提供统一的依赖注入接口。

修复内容：
- get/set 重命名为 get_extra/set_extra
- 添加 require() 方法验证依赖
- BrowserContext 和 FilesystemContext 集成到 ToolContext
- 添加 child() 方法创建上下文变体
- 添加 async close() 方法
- 添加异步版本的 create_context_from_agent
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Set

if TYPE_CHECKING:
    from ..tools.catalog import ToolCatalog
    from ..tools.mcp import MCPClient
    from ..tools.mcp_catalog import MCPCatalog


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


@dataclass
class ToolContext:
    """
    工具执行上下文 - 依赖注入容器

    修复内容：
    - get_extra/set_extra 方法仅操作 extra 字典
    - 预定义字段直接通过属性访问
    - 集成 BrowserContext 和 FilesystemContext

    使用示例:
        # 创建上下文
        context = ToolContext(
            mcp_catalog=mcp_catalog,
            mcp_client=mcp_client,
            tool_catalog=tool_catalog,
        )

        # 验证必需依赖
        context.require("mcp_catalog", "tool_catalog")

        # 获取额外参数
        value = context.get_extra("custom_key", default="default_value")

        # 设置额外参数
        context.set_extra("custom_key", "custom_value")

        # 基于现有上下文创建变体
        child_context = context.child(session_id="new_session")

        # 关闭（清理资源）
        await context.close()
    """

    mcp_catalog: Optional["MCPCatalog"] = None
    mcp_client: Optional["MCPClient"] = None
    tool_catalog: Optional["ToolCatalog"] = None

    # 额外的运行时上下文
    session_id: str | None = None
    user_id: str | None = None

    # 专用于上下文的字段
    browser: Optional[BrowserContext] = None
    filesystem: Optional[FilesystemContext] = None

    # 扩展字段（用于传递额外的依赖）
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """初始化默认值"""
        if self.browser is None:
            self.browser = BrowserContext()
        if self.filesystem is None:
            self.filesystem = FilesystemContext()

    def get_extra(self, key: str, default: Any = None) -> Any:
        """获取额外上下文中的值"""
        return self.extra.get(key, default)

    def set_extra(self, key: str, value: Any) -> None:
        """设置额外上下文中的值"""
        self.extra[key] = value

    def require(self, *keys: str) -> None:
        """验证必需依赖是否存在

        Args:
            *keys: 需要验证的字段名

        Raises:
            ValueError: 如果任何必需字段缺失
        """
        missing: list[str] = []
        for key in keys:
            if not hasattr(self, key) or getattr(self, key) is None:
                missing.append(key)

        if missing:
            raise ValueError(f"Missing required dependencies: {missing}")

    def has(self, key: str) -> bool:
        """检查字段是否存在且非空"""
        if hasattr(self, key):
            value = getattr(self, key)
            return value is not None
        return key in self.extra

    def child(self, **overrides: Any) -> "ToolContext":
        """基于现有上下文创建子上下文

        Args:
            **overrides: 要覆盖的字段

        Returns:
            新的 ToolContext 实例
        """
        # 复制当前字段
        new_context = ToolContext(
            mcp_catalog=self.mcp_catalog,
            mcp_client=self.mcp_client,
            tool_catalog=self.tool_catalog,
            session_id=self.session_id,
            user_id=self.user_id,
            browser=BrowserContext(
                manager=self.browser.manager if self.browser else None,
                tools=self.browser.tools if self.browser else None,
            )
            if self.browser
            else None,
            filesystem=FilesystemContext(
                root_path=self.filesystem.root_path if self.filesystem else None,
                allowed_paths=list(self.filesystem.allowed_paths) if self.filesystem else [],
            )
            if self.filesystem
            else None,
            extra=dict(self.extra),
        )

        # 应用覆盖
        for key, value in overrides.items():
            if hasattr(new_context, key):
                setattr(new_context, key, value)
            else:
                new_context.extra[key] = value

        return new_context

    async def close(self) -> None:
        """关闭上下文，清理资源"""
        self.mcp_catalog = None
        self.mcp_client = None
        self.tool_catalog = None
        self.browser = None
        self.filesystem = None
        self.extra.clear()


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


async def create_context_from_agent_async(agent: Any) -> ToolContext:
    """
    从Agent对象创建ToolContext（异步版本）

    如果agent属性是协程也能处理。
    """
    context = ToolContext(
        session_id=getattr(agent, "session_id", None),
        user_id=getattr(agent, "user_id", None),
    )

    # 同步属性迁移
    if hasattr(agent, "mcp_catalog"):
        value = agent.mcp_catalog
        if callable(value):
            value = await value()
        context.mcp_catalog = value

    if hasattr(agent, "mcp_client"):
        value = agent.mcp_client
        if callable(value):
            value = await value()
        context.mcp_client = value

    if hasattr(agent, "tool_catalog"):
        value = agent.tool_catalog
        if callable(value):
            value = await value()
        context.tool_catalog = value

    return context
