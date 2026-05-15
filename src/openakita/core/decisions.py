"""
Shared decision types used across core modules.

Extracted from reasoning_engine.py to break the circular dependency
between stream_accumulator <-> reasoning_engine.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DecisionType(Enum):
    """LLM 决策类型"""

    FINAL_ANSWER = "final_answer"  # 纯文本响应
    TOOL_CALLS = "tool_calls"  # 需要工具调用


@dataclass
class Decision:
    """LLM 推理决策"""

    type: DecisionType
    text_content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    thinking_content: str = ""
    raw_response: Any = None
    stop_reason: str = ""
    # 完整的 assistant_content（保留 thinking 块等）
    assistant_content: list[dict] = field(default_factory=list)
