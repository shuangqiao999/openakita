"""
向后兼容模块

提供旧 API 的包装层，确保平滑迁移到作战指挥室架构。
"""

import warnings
from .agent_wrapper import AgentWrapper

__all__ = ["AgentWrapper"]

# 发出 deprecation 警告
warnings.warn(
    "旧的 Agent API 已弃用，请迁移到新的作战指挥室架构。"
    "查看 docs/MIGRATION_GUIDE.md 了解详情。",
    DeprecationWarning,
    stacklevel=2,
)
