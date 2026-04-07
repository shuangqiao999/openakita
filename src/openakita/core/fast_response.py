"""
简单问答快速通道

实现简单问答不走LLM推理，直接匹配预置回复。
提高响应速度，降低LLM调用成本。
"""

import re
from datetime import datetime


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

    # 精确匹配问答对
    _predefined_answers: dict[str, str] = {
        "你好": "你好！有什么可以帮助你的吗？",
        "您好": "您好！有什么我可以帮您的吗？",
        "hi": "Hi! How can I help you?",
        "hello": "Hello! What can I do for you?",
        "help": "我可以帮助你完成各种任务，比如：\n- 编写和调试代码\n- 分析和生成文档\n- 执行Shell命令\n- 搜索网络信息\n- 等等\n\n直接发送消息即可开始对话。",
        "帮助": "我可以帮助你完成各种任务，比如：\n- 编写和调试代码\n- 分析和生成文档\n- 执行Shell命令\n- 搜索网络信息\n\n直接发送消息即可开始对话。",
        "你是谁": "我是 Open Akita，一个智能AI助手。我可以帮助你完成代码开发、数据分析、问题解答等各种任务。",
        "你叫什么": "我是 Open Akita，一个智能AI助手。",
        "时间": None,  # 动态生成
        "日期": None,  # 动态生成
    }

    # 模式匹配问答（正则表达式 -> 回复模板）
    _pattern_answers: list[tuple[re.Pattern, str]] = [
        (re.compile(r"^(你是|你叫).*"), "我是 Open Akita，一个智能AI助手。"),
        (re.compile(r"(谢谢|感谢|感谢你)", re.IGNORECASE), "不客气，很高兴能帮到你！"),
        (re.compile(r"^(好的|OK|好|知道了)", re.IGNORECASE), "收到！"),
        (re.compile(r"^(晚安|睡觉了|再见)", re.IGNORECASE), "晚安！好梦～"),
        (re.compile(r"^(早上好|早安|早晨)", re.IGNORECASE), "早上好！新的一天开始了，保持好状态！"),
        (
            re.compile(r"(.*)怎么(.*)", re.IGNORECASE),
            "这个问题我可以帮你解答。请具体说明你想要了解什么？",
        ),
        (re.compile(r"(.*)是什么", re.IGNORECASE), "让我来解释一下。请告诉我具体是什么？"),
        (re.compile(r"(.*)在哪里", re.IGNORECASE), "让我帮你查一下。请提供更多具体信息。"),
        (re.compile(r"(.*)多少钱", re.IGNORECASE), "价格会因具体情况而异。请告诉我更多细节。"),
    ]

    @classmethod
    def match(cls, query: str) -> str | None:
        """匹配预置回复

        Args:
            query: 用户查询

        Returns:
            预置回复，如果未匹配返回None
        """
        query = query.strip()

        # 1. 精确匹配
        if query in cls._predefined_answers:
            answer = cls._predefined_answers[query]
            if answer is None:
                # 动态生成
                if query == "时间":
                    return f"当前时间是 {datetime.now().strftime('%H:%M:%S')}"
                elif query == "日期":
                    return f"今天是 {datetime.now().strftime('%Y年%m月%d日')}"
            return answer

        # 2. 模式匹配
        for pattern, answer in cls._pattern_answers:
            if pattern.search(query):
                return answer

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

    判断请求是简单问答还是复杂任务，走不同处理流程。
    """

    # 简单关键词（短消息匹配这些词认为是简单问答）
    SIMPLE_KEYWORDS = {
        "你好",
        "您好",
        "hi",
        "hello",
        "help",
        "帮助",
        "谢谢",
        "感谢",
        "好的",
        "ok",
        "知道了",
        "晚安",
        "早安",
        "早上好",
        "天气",
        "时间",
        "日期",
    }

    # 复杂任务指示词（包含这些认为是复杂任务）
    COMPLEX_INDICATORS = {
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

    # 问句结尾（提问类通常不是简单问答）
    QUESTION_ENDINGS = {"吗", "呢", "?", "？", "怎么", "如何", "为什么"}

    @classmethod
    def classify(cls, query: str) -> str:
        """
        分类请求类型

        Args:
            query: 用户查询

        Returns:
            "simple" - 简单问答
            "complex" - 复杂任务
        """
        query_lower = query.lower().strip()
        query_len = len(query)

        # 空消息
        if not query_len:
            return "complex"

        # 1. 检查是否匹配快速回复
        if FastResponseRouter.can_handle(query):
            return "simple"

        # 2. 检查复杂指示词
        for indicator in cls.COMPLEX_INDICATORS:
            if indicator in query_lower:
                return "complex"

        # 3. 短消息且匹配简单关键词
        if query_len < 30:
            for kw in cls.SIMPLE_KEYWORDS:
                if kw in query_lower:
                    return "simple"

        # 4. 问句处理（问句通常需要LLM）
        if any(query.endswith(q) for q in cls.QUESTION_ENDINGS):
            return "complex"

        # 5. 超短消息（可能是简单打招呼）
        if query_len < 10:
            return "simple"

        # 6. 默认复杂（保守策略）
        return "complex"

    @classmethod
    def should_use_fast_path(cls, query: str) -> bool:
        """判断是否应该走快速通道

        快速通道：预置回复 -> 不调用LLM
        正常通道：LLM推理
        """
        return cls.classify(query) == "simple"


# 便捷函数
def try_fast_response(query: str) -> str | None:
    """尝试快速响应，如果可以处理则返回回复，否则返回None"""
    return FastResponseRouter.match(query)


def is_simple_request(query: str) -> bool:
    """判断是否是简单请求"""
    return RequestClassifier.should_use_fast_path(query)
