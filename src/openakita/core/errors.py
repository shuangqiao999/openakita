"""
核心异常类
"""

from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum


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


class ErrorCode(Enum):
    """错误码枚举"""

    UNKNOWN = "UNKNOWN"
    INVALID_INPUT = "INVALID_INPUT"
    TIMEOUT = "TIMEOUT"
    CHANNEL_SEND = "CHANNEL_SEND"
    CHANNEL_RECEIVE = "CHANNEL_RECEIVE"
    CHANNEL_AUTH = "CHANNEL_AUTH"
    CHANNEL_RATE_LIMIT = "CHANNEL_RATE_LIMIT"
    CHANNEL_NOT_FOUND = "CHANNEL_NOT_FOUND"
    CHANNEL_TIMEOUT = "CHANNEL_TIMEOUT"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_EXECUTE_ERROR = "TOOL_EXECUTE_ERROR"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_PERMISSION_DENIED = "TOOL_PERMISSION_DENIED"
    MCP_CONNECTION_ERROR = "MCP_CONNECTION_ERROR"
    MCP_TOOL_NOT_FOUND = "MCP_TOOL_NOT_FOUND"
    MCP_TIMEOUT = "MCP_TIMEOUT"


@dataclass
class ChannelError(Exception):
    """所有渠道异常的基类"""

    code: ErrorCode = ErrorCode.UNKNOWN
    message: str = ""
    original: Optional[Exception] = None

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "message": self.message,
            "original": str(self.original) if self.original else None,
        }


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


@dataclass
class HandlerResult:
    """Handler统一返回类型"""

    success: bool
    content: Optional[str] = None
    content_list: Optional[List[str]] = None
    error: Optional[ChannelError] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        result = {"success": self.success}
        if self.content:
            result["content"] = self.content
        if self.content_list:
            result["content_list"] = self.content_list
        if self.error:
            result["error"] = self.error.to_dict()
        if self.metadata:
            result["metadata"] = self.metadata
        return result
