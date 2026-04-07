"""
简单问答快速通道

实现简单问答不走LLM推理，直接匹配预置回复。
提高响应速度，降低LLM调用成本。

修复内容：
- 包含匹配：精确匹配改为包含匹配
- 重新设计分类优先级
- 移除泛匹配模式
- 动态生成分离到独立处理函数
- 添加长度检查
- 添加统计监控
"""

import re
from datetime import datetime
from typing import Callable, Optional

# 统计监控
_stats = {"hits": 0, "misses": 0, "false_positives": 0}


class FastResponseRouter:
    """
    简单问答快速路由器

    使用示例:
        from openakita.core.fast_response import FastResponseRouter, RequestClassifier

        # 检查是否可以直接处理
        if FastResponseRouter.can_handle(query):
            response = FastResponseRouter.match(query)
            return response

        # 或者使用分类器判断
        if RequestClassifier.classify(query) == "simple":
            # 走快速通道
            pass
    """

    # 精确匹配问答对（字符串 -> 回复）
    _predefined_answers: dict[str, str] = {
        "你好": "你好！有什么可以帮助你的吗？",
        "您好": "您好！有什么我可以帮您的吗？",
        "hi": "Hi! How can I help you?",
        "hello": "Hello! What can I do for you?",
        "help": "我可以帮助你完成各种任务，比如：\n- 编写和调试代码\n- 分析和生成文档\n- 执行Shell命令\n- 搜索网络信息\n- 等等\n\n直接发送消息即可开始对话。",
        "帮助": "我可以帮助你完成各种任务，比如：\n- 编写和调试代码\n- 分析和生成文档\n- 执行Shell命令\n- 搜索网络信息\n\n直接发送消息即可开始对话。",
        "你是谁": "我是 Open Akita，一个智能AI助手。我可以帮助你完成代码开发、数据分析、问题解答等各种任务。",
        "你叫什么": "我是 Open Akita，一个智能AI助手。",
    }

    # 动态处理函数
    _dynamic_handlers: dict[str, Callable[[], str]] = {
        "时间": lambda: f"当前时间是 {datetime.now().strftime('%H:%M:%S')}",
        "日期": lambda: f"今天是 {datetime.now().strftime('%Y年%m月%d日')}",
    }

    # 模式匹配问答（正则表达式 -> 回复模板）
    # 移除了泛匹配模式，只保留明确的匹配
    _pattern_answers: list[tuple[re.Pattern, str]] = [
        (re.compile(r"^(你是|你叫).*"), "我是 Open Akita，一个智能AI助手。"),
        (re.compile(r"(谢谢|感谢|感谢你)", re.IGNORECASE), "不客气，很高兴能帮到你！"),
        (re.compile(r"^(好的|OK|好|知道了)", re.IGNORECASE), "收到！"),
        (re.compile(r"^(晚安|睡觉了|再见)", re.IGNORECASE), "晚安！好梦～"),
        (re.compile(r"^(早上好|早安|早晨)", re.IGNORECASE), "早上好！新的一天开始了，保持好状态！"),
    ]

    # 最大输入长度限制
    _MAX_QUERY_LENGTH = 500

    @classmethod
    def match(cls, query: str) -> str | None:
        """匹配预置回复

        包含匹配 + 长度保护

        Args:
            query: 用户查询

        Returns:
            预置回复，如果未匹配返回None
        """
        global _stats

        # 长度检查
        if len(query) > cls._MAX_QUERY_LENGTH:
            return None

        query = query.strip()
        if not query:
            return None

        # 1. 精确匹配
        if query in cls._predefined_answers:
            _stats["hits"] += 1
            return cls._predefined_answers[query]

        # 2. 动态生成
        if query in cls._dynamic_handlers:
            _stats["hits"] += 1
            return cls._dynamic_handlers[query]()

        # 3. 包含匹配（短查询）
        if len(query) < 20:
            for key, answer in cls._predefined_answers.items():
                if key in query:
                    _stats["hits"] += 1
                    return answer

        # 4. 模式匹配
        for pattern, answer in cls._pattern_answers:
            if pattern.search(query):
                _stats["hits"] += 1
                return answer

        _stats["misses"] += 1
        return None

    @classmethod
    def can_handle(cls, query: str) -> bool:
        """检查是否可以处理此查询

        Args:
            query: 用户查询

        Returns:
            True 如果可以直接处理
        """
        return cls.match(query) is not None


class RequestClassifier:
    """
    请求分类器

    重新设计分类优先级：
    1. 感谢类 > 打招呼类 > 明确操作词 > 问句 > 默认

    判断请求是简单问答还是复杂任务，走不同处理流程。
    """

    # 感谢类（最高优先级）
    THANKS_KEYWORDS = {"谢谢", "感谢", "谢谢你", "感谢你", "谢谢您"}

    # 打招呼类
    GREETING_KEYWORDS = {"你好", "您好", "hi", "hello", "嗨", "嘿", "早", "晚安"}

    # 明确操作词（复杂任务）
    ACTION_KEYWORDS = {
        "分析",
        "总结",
        "生成",
        "代码",
        "写",
        "创建",
        "搜索",
        "开发",
        "实现",
        "修复",
        "优化",
        "重构",
        "测试",
        "部署",
        "配置",
        "安装",
        "编译",
        "运行",
        "执行",
        "转换",
        "提取",
        "比较",
        "评估",
        "设计",
        "规划",
    }

    # 问句结尾（需要LLM）
    QUESTION_ENDINGS = {"吗", "呢", "?", "？", "怎么", "如何", "为什么"}

    @classmethod
    def classify(cls, query: str) -> str:
        """
        分类请求类型

        优先级：感谢 > 打招呼 > 明确操作 > 问句 > 默认

        Args:
            query: 用户查询

        Returns:
            "simple" - 简单问答
            "complex" - 复杂任务
        """
        global _stats

        query_lower = query.lower().strip()
        query_len = len(query)

        # 空消息
        if not query_len:
            return "complex"

        # 1. 检查是否匹配快速回复（优先）
        if FastResponseRouter.can_handle(query):
            _stats["hits"] += 1
            return "simple"

        # 2. 感谢类（最高优先级）
        for kw in cls.THANKS_KEYWORDS:
            if kw in query_lower:
                return "simple"

        # 3. 打招呼类
        if query_len < 30:
            for kw in cls.GREETING_KEYWORDS:
                if kw in query_lower:
                    return "simple"

        # 4. 明确操作词（复杂任务）
        for indicator in cls.ACTION_KEYWORDS:
            if indicator in query_lower:
                # 如果同时有问句结尾，可能只是询问如何做某事
                if any(query.endswith(q) for q in cls.QUESTION_ENDINGS):
                    continue
                return "complex"

        # 5. 问句处理（问句通常需要LLM）
        if any(query.endswith(q) for q in cls.QUESTION_ENDINGS):
            return "complex"

        # 6. 超短消息（可能是简单打招呼）
        if query_len < 10:
            return "simple"

        # 7. 默认复杂（保守策略）
        return "complex"

    @classmethod
    def should_use_fast_path(cls, query: str) -> bool:
        """判断是否应该走快速通道

        快速通道：预置回复 -> 不调用LLM
        正常通道：LLM推理
        """
        return cls.classify(query) == "simple"


def try_fast_response(query: str) -> str | None:
    """尝试快速响应，如果可以处理则返回回复，否则返回None"""
    return FastResponseRouter.match(query)


def is_simple_request(query: str) -> bool:
    """判断是否是简单请求"""
    return RequestClassifier.should_use_fast_path(query)


def get_stats() -> dict:
    """获取统计信息"""
    global _stats
    total = _stats["hits"] + _stats["misses"]
    return {
        "hits": _stats["hits"],
        "misses": _stats["misses"],
        "hit_rate": _stats["hits"] / total if total > 0 else 0.0,
    }


def reset_stats() -> None:
    """重置统计"""
    global _stats
    _stats = {"hits": 0, "misses": 0, "false_positives": 0}
