"""
IntentAnalyzer — Unified intent analysis via LLM.

Replaces the separate _compile_prompt() + _should_compile_prompt() with a single
LLM call that outputs structured intent, task definition, tool hints, and memory
keywords. All messages go through the LLM — no rule-based shortcut layer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .brain import Brain

logger = logging.getLogger(__name__)


class IntentType(Enum):
    CHAT = "chat"
    QUERY = "query"
    TASK = "task"
    FOLLOW_UP = "follow_up"
    COMMAND = "command"


@dataclass
class ComplexitySignal:
    multi_file_change: bool = False
    cross_module: bool = False
    ambiguous_scope: bool = False
    destructive_potential: bool = False
    multi_step_required: bool = False
    should_suggest_plan: bool = False
    score: int = 0

    def __post_init__(self):
        self.score = (
            self.multi_file_change * 2
            + self.cross_module * 2
            + self.ambiguous_scope * 2
            + self.destructive_potential * 1
            + self.multi_step_required * 1
        )
        self.should_suggest_plan = self.score >= 3


@dataclass
class IntentResult:
    intent: IntentType
    confidence: float
    task_definition: str
    task_type: str
    tool_hints: list[str] = field(default_factory=list)
    recommended_tools: list[str] = field(default_factory=list)
    memory_keywords: list[str] = field(default_factory=list)
    force_tool: bool = False
    todo_required: bool = False
    raw_output: str = ""
    fast_reply: bool = False
    complexity: ComplexitySignal | None = None
    suggest_plan: bool = False


INTENT_ANALYZER_SYSTEM = """You are an intent analyzer. Analyze the user's message and output structured intent information.

Output in YAML format:
```yaml
intent: <chat|query|task|follow_up|command>
task_type: <simple|compound|question|other>
goal: <what the user wants to achieve>
tool_hints: [<optional tool names to use>]
recommended_tools: [<tools that might be helpful>]
memory_keywords: [<keywords to search in memory>]
```

Rules:
- intent=chat: simple greetings, confirmations, small talk
- intent=query: looking for information, facts
- intent=task: wants to accomplish something, needs tools
- intent=follow_up: continuation of previous conversation
- intent=command: wants to execute a specific command

task_type:
- simple: single action, can be done directly
- compound: multiple steps required, suggest plan mode
- question: seeking information
- other: unclear intent
"""


_THINKING_TAG_PATTERN = re.compile(r"<thinking>.*?</thinking>", re.DOTALL)


def _strip_thinking_tags(text: str) -> str:
    return _THINKING_TAG_PATTERN.sub("", text).strip()


# ---------------------------------------------------------------------------
# Rule-based fast-path for obvious chat messages
# ---------------------------------------------------------------------------

_GREETING_PATTERNS: set[str] = {
    # Chinese greetings / confirmations / farewells
    "你好",
    "您好",
    "你好呀",
    "你好啊",
    "嗨",
    "哈喽",
    "hello",
    "hi",
    "hey",
    "嗯",
    "嗯嗯",
    "好",
    "好的",
    "行",
    "ok",
    "可以",
    "收到",
    "了解",
    "知道了",
    "谢谢",
    "谢了",
    "感谢",
    "thanks",
    "thank you",
    "thx",
    "感恩",
    "、多谢",
    "再见",
    "拜拜",
    "bye",
    "晚安",
    "早安",
    "早",
    "早上好",
    "下午好",
    "晚上好",
    "睡觉啦",
    "在吗",
    "在不在",
    "你在吗",
    "还在吗",
    "哈哈",
    "哈哈哈",
    "笑死",
    "666",
    "牛",
    "厉害",
    "太强了",
    "佩服",
    "?",
    "？",
    "!",
    "！",
    "。。",
    "...",
    "辛苦啦",
    "麻烦啦",
    "搞定啦",
    "完成啦",
    "好棒",
    "赞",
    "点赞",
    "收到啦",
    "明白啦",
    "懂了懂了",
    "了解了解",
}

# Fast answer patterns for simple queries that can be answered directly
# Maps pattern -> (response_type, tool_hint)
_QUICK_ANSWER_PATTERNS: dict[str, tuple[str, str | None]] = {
    "现在几点": ("time", None),
    "几点啦": ("time", None),
    "现在时间": ("time", None),
    "今天几号": ("date", None),
    "今天日期": ("date", None),
    "今天是": ("date", None),
    "天气": ("weather", "web_search"),
    "查天气": ("weather", "web_search"),
}

# When conversation history exists, only these unambiguous strings use the fast-path;
# punctuation and short confirmations are analyzed by the LLM (may be follow-ups).
_SAFE_WITH_HISTORY: frozenset[str] = frozenset(
    {
        "你好",
        "您好",
        "你好呀",
        "你好啊",
        "嗨",
        "哈喽",
        "hello",
        "hi",
        "hey",
        "谢谢",
        "谢了",
        "感谢",
        "thanks",
        "thank you",
        "thx",
        "再见",
        "拜拜",
        "bye",
        "晚安",
        "早安",
        "早",
        "早上好",
        "下午好",
        "晚上好",
    }
)

_FAST_CHAT_MAX_LEN = 12


def _try_fast_chat_shortcut(message: str, has_history: bool = False) -> IntentResult | None:
    """Rule-based shortcut: if message is an obvious greeting/confirmation,
    return CHAT intent immediately without LLM call.

    Returns None if the message doesn't match (should go through normal LLM analysis).
    """
    stripped = message.strip()

    if len(stripped) > _FAST_CHAT_MAX_LEN:
        return None

    normalized = stripped.lower().rstrip("~～。.!！?？、,，")

    # If there's conversation history, only match unambiguous greetings,
    # NOT punctuation or short confirmations that could be follow-ups
    if has_history:
        # With history, only pure greetings are safe to fast-path
        # Things like "？", "!", "好的", "嗯" could be follow-ups
        if normalized not in _SAFE_WITH_HISTORY:
            return None  # Ambiguous with history → go through LLM

    if normalized in _GREETING_PATTERNS:
        logger.info(f"[IntentAnalyzer] Fast-path: '{stripped}' matched as CHAT (rule-based)")
        return IntentResult(
            intent=IntentType.CHAT,
            confidence=1.0,
            task_definition="",
            task_type="other",
            tool_hints=[],
            memory_keywords=[],
            force_tool=False,
            todo_required=False,
            raw_output="[fast-chat-shortcut]",
            fast_reply=True,
        )

    if (
        not has_history
        and len(stripped) <= 6
        and all(not c.isalnum() or c in "0123456789" for c in stripped)
    ):
        logger.info(f"[IntentAnalyzer] Fast-path: '{stripped}' is pure punctuation/emoji → CHAT")
        return IntentResult(
            intent=IntentType.CHAT,
            confidence=0.9,
            task_definition="",
            task_type="other",
            tool_hints=[],
            memory_keywords=[],
            force_tool=False,
            todo_required=False,
            raw_output="[fast-chat-shortcut-punctuation]",
            fast_reply=True,
        )

    return None


def _try_fast_query_shortcut(message: str) -> IntentResult | None:
    """Fast-path for simple queries that can be answered without LLM or with specific tools.

    Returns IntentResult if matched, None otherwise.
    """
    import datetime

    stripped = message.strip()
    normalized = stripped.lower()

    if normalized in _QUICK_ANSWER_PATTERNS:
        query_type, tool_hint = _QUICK_ANSWER_PATTERNS[normalized]

        if query_type == "time":
            current_time = datetime.datetime.now().strftime("%H:%M:%S")
            response = f"现在是 {current_time}"
            logger.info(f"[IntentAnalyzer] Fast-query: '{stripped}' → time query")
            return IntentResult(
                intent=IntentType.CHAT,
                confidence=1.0,
                task_definition=response,
                task_type="question",
                tool_hints=[tool_hint] if tool_hint else [],
                memory_keywords=[],
                force_tool=False,
                todo_required=False,
                raw_output="[fast-query-shortcut]",
                fast_reply=True,
            )

        if query_type == "date":
            current_date = datetime.datetime.now().strftime("%Y年%m月%d日 %A")
            response = f"今天是 {current_date}"
            logger.info(f"[IntentAnalyzer] Fast-query: '{stripped}' → date query")
            return IntentResult(
                intent=IntentType.CHAT,
                confidence=1.0,
                task_definition=response,
                task_type="question",
                tool_hints=[tool_hint] if tool_hint else [],
                memory_keywords=[],
                force_tool=False,
                todo_required=False,
                raw_output="[fast-query-shortcut]",
                fast_reply=True,
            )

        if query_type == "weather":
            logger.info(f"[IntentAnalyzer] Fast-query: '{stripped}' → weather (needs web_search)")
            return IntentResult(
                intent=IntentType.QUERY,
                confidence=1.0,
                task_definition=f"查询{stripped}的天气",
                task_type="question",
                tool_hints=[tool_hint] if tool_hint else [],
                memory_keywords=["天气"],
                force_tool=True,
                todo_required=False,
                raw_output="[fast-query-shortcut]",
                fast_reply=False,
            )

    return None


class IntentAnalyzer:
    """LLM-based intent analyzer. All messages go through LLM analysis."""

    def __init__(self, brain: Brain):
        self.brain = brain

    async def analyze(
        self,
        message: str,
        session_context: Any = None,
        has_history: bool = False,
    ) -> IntentResult:
        """Analyze user message intent. Rule-based shortcuts first, then LLM analysis."""
        fast_query_result = _try_fast_query_shortcut(message)
        if fast_query_result is not None:
            return fast_query_result

        fast_result = _try_fast_chat_shortcut(message, has_history=has_history)
        if fast_result is not None:
            return fast_result

        try:
            response = await self.brain.compiler_think(
                prompt=message,
                system=INTENT_ANALYZER_SYSTEM,
            )

            raw_output = _strip_thinking_tags(response.content).strip() if response.content else ""
            if not raw_output:
                logger.warning("[IntentAnalyzer] Empty LLM response, using default")
                return _make_default(message)

            logger.info(f"[IntentAnalyzer] Raw output: {raw_output[:200]}")
            return _parse_intent_output(raw_output, message)

        except Exception as e:
            logger.warning(f"[IntentAnalyzer] LLM analysis failed: {e}, using default")
            return _make_default(message)


def _make_default(message: str) -> IntentResult:
    """Fallback: behaves like the old flow (TASK + full tools + ForceToolCall)."""
    return IntentResult(
        intent=IntentType.TASK,
        confidence=0.0,
        task_definition=message[:600],
        task_type="action",
        tool_hints=[],
        memory_keywords=[],
        force_tool=True,
        todo_required=False,
        raw_output="",
    )


def _parse_intent_output(raw_output: str, message: str) -> IntentResult:
    """Parse YAML output from IntentAnalyzer LLM into IntentResult."""
    lines = raw_output.splitlines()

    extracted: dict[str, str] = {}
    current_key = ""
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            continue

        kv_match = re.match(r"^(\w[\w_]*):\s*(.*)", stripped)
        if kv_match and kv_match.group(1) in (
            "intent",
            "task_type",
            "goal",
            "tool_hints",
            "recommended_tools",
            "memory_keywords",
            "constraints",
            "inputs",
            "output_requirements",
            "risks_or_ambiguities",
        ):
            if current_key:
                extracted[current_key] = "\n".join(current_lines).strip()
            current_key = kv_match.group(1)
            current_lines = [kv_match.group(2).strip()]
        elif current_key:
            current_lines.append(stripped)

    if current_key:
        extracted[current_key] = "\n".join(current_lines).strip()

    intent_str = extracted.get("intent", "task").lower().strip()
    intent_map = {
        "chat": IntentType.CHAT,
        "query": IntentType.QUERY,
        "task": IntentType.TASK,
        "follow_up": IntentType.FOLLOW_UP,
        "command": IntentType.COMMAND,
    }
    intent = intent_map.get(intent_str, IntentType.TASK)

    task_type = extracted.get("task_type", "other").strip()

    goal = extracted.get("goal", "").strip()
    task_definition = _build_task_definition(extracted, max_chars=600)

    tool_hints = _parse_list(extracted.get("tool_hints", ""))
    recommended_tools = _parse_list(extracted.get("recommended_tools", ""))
    memory_keywords = _parse_list(extracted.get("memory_keywords", ""))

    force_tool = intent in (IntentType.TASK,) and task_type not in ("question", "other")
    todo_required = task_type == "compound"

    result = IntentResult(
        intent=intent,
        confidence=1.0,
        task_definition=task_definition or goal or message[:200],
        task_type=task_type,
        tool_hints=tool_hints,
        recommended_tools=recommended_tools,
        memory_keywords=memory_keywords,
        force_tool=force_tool,
        todo_required=todo_required,
        raw_output=raw_output,
    )

    # Complexity analysis for plan mode suggestion
    if intent in (IntentType.TASK,):
        result.complexity = _analyze_complexity(message, result)
        result.suggest_plan = result.complexity.should_suggest_plan
        if result.suggest_plan:
            logger.info(
                f"[IntentAnalyzer] Complex task detected (score={result.complexity.score}), "
                f"suggesting Plan mode"
            )

    return result


def _build_task_definition(extracted: dict[str, str], max_chars: int = 600) -> str:
    """Build a compact task definition string from extracted YAML fields."""
    parts: list[str] = []
    for key in ("goal", "task_type", "constraints", "output_requirements"):
        val = extracted.get(key, "").strip()
        if val and val not in ("[]", ""):
            parts.append(f"{key}: {val}")
        if sum(len(p) + 3 for p in parts) >= max_chars:
            break
    summary = " | ".join(parts)
    return summary[:max_chars] if len(summary) > max_chars else summary


def _parse_list(value: str) -> list[str]:
    """Parse a YAML list value into a Python list of strings."""
    value = value.strip()
    if not value or value == "[]":
        return []

    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]

    items = []
    for line in value.split("\n"):
        line = line.strip()
        if line.startswith("- "):
            items.append(line[2:].strip().strip("'\""))
        elif line and line not in ("[]",):
            items.append(line.strip("'\""))
    return items


# ---------------------------------------------------------------------------
# Complex task detection
# ---------------------------------------------------------------------------

_REFACTOR_KEYWORDS = [
    "重构",
    "refactor",
    "redesign",
    "改造",
    "迁移",
    "migration",
    "migrate",
    "重写",
    "rewrite",
]
_GLOBAL_KEYWORDS = [
    "全部",
    "所有",
    "整个项目",
    "across the codebase",
    "entire",
    "all files",
    "批量",
    "全局",
]
_ARCHITECTURE_KEYWORDS = [
    "架构",
    "设计方案",
    "技术选型",
    "architecture",
    "design",
    "系统设计",
    "system design",
]
_RESEARCH_KEYWORDS = [
    "调研",
    "分析",
    "对比",
    "evaluate",
    "compare",
    "research",
    "review",
    "评估",
    "综合分析",
]
_MULTI_FILE_KEYWORDS = [
    "多个文件",
    "multiple files",
    "所有文件",
    "每个文件",
    "across files",
    "跨文件",
]


def _analyze_complexity(message: str, intent_result: IntentResult) -> ComplexitySignal:
    """Analyze message complexity to determine if Plan mode should be suggested."""
    msg = message.lower()
    signal = ComplexitySignal()

    # Multi-file change detection
    if any(kw in msg for kw in _MULTI_FILE_KEYWORDS) or any(kw in msg for kw in _GLOBAL_KEYWORDS):
        signal.multi_file_change = True

    # Cross-module detection
    if any(kw in msg for kw in _ARCHITECTURE_KEYWORDS):
        signal.cross_module = True

    # Ambiguous scope detection
    if any(kw in msg for kw in _REFACTOR_KEYWORDS):
        signal.ambiguous_scope = True
    if any(kw in msg for kw in _RESEARCH_KEYWORDS):
        signal.ambiguous_scope = True

    # Destructive potential
    destructive_words = ["删除", "清空", "重置", "drop", "delete all", "remove all", "清除"]
    if any(kw in msg for kw in destructive_words):
        signal.destructive_potential = True

    # Multi-step required (from intent analysis)
    if intent_result.task_type == "compound" or len(message) > 200:
        signal.multi_step_required = True

    return signal
