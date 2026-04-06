"""
快速回复处理器 - 0 Token，<10ms 响应
"""

import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class IntentType(Enum):
    GREETING = "greeting"
    FAREWELL = "farewell"
    THANKS = "thanks"
    AFFIRMATION = "affirmation"
    NEGATION = "negation"
    QUESTION = "question"
    COMPLIMENT = "compliment"
    APOLOGY = "apology"
    SMALL_TALK = "small_talk"


@dataclass
class QuickReplyRule:
    """快速回复规则"""

    patterns: list[str]
    intent: IntentType
    response: str | Callable
    priority: int = 0


class QuickReplyHandler:
    """简单对话立即回复，不走 LLM"""

    def __init__(self):
        self.rules: list[QuickReplyRule] = []
        self._init_default_rules()

    def _init_default_rules(self):
        self.rules.append(
            QuickReplyRule(
                patterns=[
                    r"^(你好|您好|hi|hello|嗨|哈喽|在吗|在不在|喂)$",
                    r"^(早上好|下午好|晚上好|早安|午安|晚安)$",
                    r"^(hey|hi there|hello there|hi!|hello!|hi there!)$",
                ],
                intent=IntentType.GREETING,
                response=lambda m: self._get_greeting_response(m.group(0) if m else "你好"),
                priority=10,
            )
        )

        self.rules.append(
            QuickReplyRule(
                patterns=[
                    r"^(再见|拜拜|bye|see you|回头见|88|白白)$",
                    r"^(我走了|先撤了|下了|睡觉了|睡了)$",
                ],
                intent=IntentType.FAREWELL,
                response="再见！有问题随时来找我～ 👋",
                priority=9,
            )
        )

        self.rules.append(
            QuickReplyRule(
                patterns=[
                    r"^(谢谢|感谢|多谢|thanks|thank you|thx|3q)$",
                    r"^(谢谢你|感谢你|多谢你|麻烦你了)$",
                    r"^(辛苦了|劳驾了)$",
                ],
                intent=IntentType.THANKS,
                response="不客气！很高兴能帮到你～ 😊",
                priority=8,
            )
        )

        self.rules.append(
            QuickReplyRule(
                patterns=[
                    r"^(好的|OK|ok|嗯|哦|知道了|明白了|懂了|收到|了解)$",
                    r"^(可以|行|没问题|当然|对|是的|是啊)$",
                    r"^(嗯嗯|哦哦|好的好的|okok)$",
                ],
                intent=IntentType.AFFIRMATION,
                response="好的！有需要随时叫我～",
                priority=7,
            )
        )

        self.rules.append(
            QuickReplyRule(
                patterns=[
                    r"^(不|不要|不行|不好|不对|不是|不可以)$",
                    r"^(算了|不用了|不需要|没关系)$",
                ],
                intent=IntentType.NEGATION,
                response="好的，那有什么其他我可以帮你的吗？",
                priority=7,
            )
        )

        self.rules.append(
            QuickReplyRule(
                patterns=[
                    r"^(你是谁|你叫什么|你是什么|介绍一下你自己)$",
                ],
                intent=IntentType.QUESTION,
                response="我是 Open Akita，你的智能助手！可以帮你处理代码、搜索信息、管理文件等～ 😊",
                priority=6,
            )
        )

        self.rules.append(
            QuickReplyRule(
                patterns=[
                    r"^(你能做什么|你会什么|有什么功能|能帮我什么)$",
                ],
                intent=IntentType.QUESTION,
                response="""我能帮你做很多事情：
📝 代码编写和调试
🔍 信息搜索和整理
📁 文件管理和编辑
🌐 网页浏览和操作
🎨 图片生成和处理
💬 日常聊天和陪伴
有什么需要尽管说！""",
                priority=6,
            )
        )

        self.rules.append(
            QuickReplyRule(
                patterns=[
                    r"^(你真棒|你好厉害|你真聪明|太强了|厉害了)$",
                    r"^(牛逼|牛啊|666|厉害厉害)$",
                ],
                intent=IntentType.COMPLIMENT,
                response="😊 谢谢夸奖！我会继续努力的～",
                priority=5,
            )
        )

        self.rules.append(
            QuickReplyRule(
                patterns=[
                    r"^(对不起|抱歉|不好意思|sorry|我的错)$",
                ],
                intent=IntentType.APOLOGY,
                response="没关系！有什么我可以帮你的吗？",
                priority=5,
            )
        )

        self.rules.append(
            QuickReplyRule(
                patterns=[
                    r"^(今天天气|天气怎么样|热吗|冷吗|下雨了)$",
                ],
                intent=IntentType.SMALL_TALK,
                response="我暂时无法获取实时天气信息。你可以开启天气工具来查询～ 🌤️",
                priority=3,
            )
        )

        self.rules.append(
            QuickReplyRule(
                patterns=[
                    r"^(吃了吗|吃饭没|饿了吗)$",
                ],
                intent=IntentType.SMALL_TALK,
                response="作为 AI，我不用吃饭啦～ 你呢？要推荐美食吗？🍜",
                priority=3,
            )
        )

        self.rules.append(
            QuickReplyRule(
                patterns=[
                    r"^(在干嘛|干嘛呢|忙啥呢)$",
                ],
                intent=IntentType.SMALL_TALK,
                response="在等你找我聊天呢～ 😊 有什么想聊的？",
                priority=3,
            )
        )

        self.rules.sort(key=lambda x: x.priority, reverse=True)

    def _get_greeting_response(self, greeting: str) -> str:
        responses = {
            "早上好": [
                "早上好！☀️ 新的一天开始了，有什么我可以帮你的吗？",
                "早安！今天也要元气满满哦～",
            ],
            "下午好": ["下午好！🌤️ 今天过得怎么样？", "下午好！需要来杯咖啡提神吗？☕"],
            "晚上好": ["晚上好！🌙 今天辛苦啦～", "晚上好！需要放松一下吗？"],
            "晚安": ["晚安！🌙 好梦～", "晚安啦，明天见！💤"],
            "default": ["你好！有什么可以帮你的吗？😊", "嗨！我在呢～", "你好呀！今天想聊点什么？"],
        }

        if greeting in responses:
            return random.choice(responses[greeting])
        return random.choice(responses["default"])

    def handle(self, text: str) -> tuple[str, None] | tuple[None, dict] | None:
        """
        处理消息
        返回 (response, None) 表示直接回复
        返回 (None, tool_call) 表示需要调用工具
        返回 None 表示需要 LLM 处理
        """
        text_clean = text.strip()

        for rule in self.rules:
            for pattern in rule.patterns:
                match = re.match(pattern, text_clean, re.IGNORECASE)
                if match:
                    response = rule.response(match) if callable(rule.response) else rule.response
                    return (response, None)

        return None

    def add_rule(self, rule: QuickReplyRule):
        """动态添加规则"""
        self.rules.append(rule)
        self.rules.sort(key=lambda x: x.priority, reverse=True)
