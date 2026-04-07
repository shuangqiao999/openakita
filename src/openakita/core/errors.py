"""
核心异常类

修复内容：
- 移除 @dataclass 装饰器，手动实现 __init__
- UserCancelledError 继承 ChannelError
- 添加更多错误码
- HandlerResult 改为泛型，添加工厂方法
- 添加 include_original 参数
- 添加 retryable 和 user_friendly_message
"""

from dataclasses import dataclass, field
from typing import Any, Generic, Optional, TypeVar, Union
from enum import Enum


class ErrorCode(Enum):
    """错误码枚举"""

    # 通用
    UNKNOWN = "UNKNOWN"
    INVALID_INPUT = "INVALID_INPUT"
    TIMEOUT = "TIMEOUT"

    # 渠道相关
    CHANNEL_SEND = "CHANNEL_SEND"
    CHANNEL_RECEIVE = "CHANNEL_RECEIVE"
    CHANNEL_AUTH = "CHANNEL_AUTH"
    CHANNEL_RATE_LIMIT = "CHANNEL_RATE_LIMIT"
    CHANNEL_NOT_FOUND = "CHANNEL_NOT_FOUND"
    CHANNEL_TIMEOUT = "CHANNEL_TIMEOUT"

    # 工具相关
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_EXECUTE_ERROR = "TOOL_EXECUTE_ERROR"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_PERMISSION_DENIED = "TOOL_PERMISSION_DENIED"

    # MCP相关
    MCP_CONNECTION_ERROR = "MCP_CONNECTION_ERROR"
    MCP_TOOL_NOT_FOUND = "MCP_TOOL_NOT_FOUND"
    MCP_TIMEOUT = "MCP_TIMEOUT"

    # 缓存相关
    CACHE_ERROR = "CACHE_ERROR"
    CACHE_MISS = "CACHE_MISS"

    # 并行执行相关
    PARALLEL_ERROR = "PARALLEL_ERROR"
    PARALLEL_TIMEOUT = "PARALLEL_TIMEOUT"
    CONNECTION_POOL_EXHAUSTED = "CONNECTION_POOL_EXHAUSTED"

    # 快速响应相关
    FAST_RESPONSE_NOT_FOUND = "FAST_RESPONSE_NOT_FOUND"


class UserCancelledError(Exception):
    """用户主动取消当前任务。

    当用户发送停止指令（如"停止"、"stop"、"取消"）时抛出，
    用于中断正在执行的 LLM 调用或工具执行。

    Attributes:
        reason: 取消原因（通常是用户发送的原始指令）
        source: 取消发生的阶段 ("llm_call" / "tool_exec")
    """

    def __init__(self, reason: str = "", source: str = ""):
        self.reason = reason
        self.source = source
        super().__init__(f"User cancelled ({source}): {reason}")


class ChannelError(Exception):
    """所有渠道异常的基类"""

    def __init__(
        self,
        code: ErrorCode = ErrorCode.UNKNOWN,
        message: str = "",
        original: Optional[Exception] = None,
    ):
        self.code = code
        self.message = message
        self.original = original
        super().__init__(f"[{self.code.value}] {message}")

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"

    def to_dict(self, include_original: bool = False) -> dict:
        """序列化错误

        Args:
            include_original: 是否包含原始异常信息
        """
        result = {
            "code": self.code.value,
            "message": self.message,
        }
        if include_original and self.original:
            result["original"] = str(self.original)
        return result


class SendError(ChannelError):
    """发送消息失败"""

    def __init__(self, message: str, original: Optional[Exception] = None):
        super().__init__(code=ErrorCode.CHANNEL_SEND, message=message, original=original)


class ReceiveError(ChannelError):
    """接收消息失败"""

    def __init__(self, message: str, original: Optional[Exception] = None):
        super().__init__(code=ErrorCode.CHANNEL_RECEIVE, message=message, original=original)


class AuthError(ChannelError):
    """认证失败"""

    def __init__(self, message: str, original: Optional[Exception] = None):
        super().__init__(code=ErrorCode.CHANNEL_AUTH, message=message, original=original)


class RateLimitError(ChannelError):
    """频率限制"""

    def __init__(self, message: str, original: Optional[Exception] = None):
        super().__init__(code=ErrorCode.CHANNEL_RATE_LIMIT, message=message, original=original)


T = TypeVar("T")


@dataclass
class HandlerResult(Generic[T]):
    """Handler统一返回类型（泛型版本）"""

    success: bool
    content: Optional[T] = None
    error: Optional[ChannelError] = None
    metadata: dict = field(default_factory=dict)

    # 额外字段
    retryable: bool = False
    user_friendly_message: Optional[str] = None

    @classmethod
    def ok(cls, content: T, **kwargs: Any) -> "HandlerResult[T]":
        """创建成功结果"""
        return cls(success=True, content=content, **kwargs)

    @classmethod
    def err(
        cls,
        error: ChannelError,
        user_message: Optional[str] = None,
        retryable: bool = False,
    ) -> "HandlerResult[None]":
        """创建错误结果"""
        return cls(
            success=False,
            error=error,
            user_friendly_message=user_message or error.message,
            retryable=retryable,
        )

    def unwrap(self) -> T:
        """获取内容，如果失败则抛出异常"""
        if not self.success:
            raise self.error or Exception("HandlerResult contains an error")
        return self.content

    def unwrap_or(self, default: T) -> T:
        """获取内容或默认值"""
        if self.success:
            return self.content
        return default

    def to_dict(self, include_original: bool = False) -> dict:
        """序列化结果"""
        result = {"success": self.success}
        if self.content is not None:
            result["content"] = self.content
        if self.error:
            result["error"] = self.error.to_dict(include_original)
        if self.metadata:
            result["metadata"] = self.metadata
        if self.retryable:
            result["retryable"] = self.retryable
        if self.user_friendly_message:
            result["user_friendly_message"] = self.user_friendly_message
        return result
