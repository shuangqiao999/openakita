"""
智能工具路由器 - 无需 LLM 的工具调用
"""

import re
from dataclasses import dataclass
from enum import Enum


class ToolCategory(Enum):
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"
    FILE_SEARCH = "file_search"
    FILE_LIST = "file_list"
    FILE_EDIT = "file_edit"

    CODE_RUN = "code_run"
    CODE_DEBUG = "code_debug"
    CODE_EXPLAIN = "code_explain"
    CODE_FORMAT = "code_format"

    WEB_SEARCH = "web_search"
    WEB_OPEN = "web_open"
    WEB_SCRAPE = "web_scrape"
    DOWNLOAD = "download"

    SYSTEM_INFO = "system_info"
    SYSTEM_COMMAND = "system_command"
    PROCESS_LIST = "process_list"

    CALCULATOR = "calculator"
    UNIT_CONVERT = "unit_convert"
    DATE_TIME = "date_time"

    TEXT_SUMMARIZE = "text_summarize"
    TEXT_TRANSLATE = "text_translate"
    TEXT_FORMAT = "text_format"

    IMAGE_GENERATE = "image_generate"
    IMAGE_EDIT = "image_edit"

    SEND_EMAIL = "send_email"
    SEND_MESSAGE = "send_message"

    REMINDER = "reminder"
    TRANSLATE = "translate"
    WEATHER = "weather"
    NEWS = "news"
    BROWSER = "browser"


@dataclass
class ToolConfig:
    name: str
    category: ToolCategory
    keywords: list[str]
    param_patterns: dict[str, str]
    description: str = ""
    requires_confirmation: bool = False


class ToolRouter:
    """智能工具路由器 - 关键词匹配 + 参数提取"""

    def __init__(self):
        self.tools: dict[str, ToolConfig] = {}
        self._init_default_tools()

    def _init_default_tools(self):
        self.tools["read_file"] = ToolConfig(
            name="read_file",
            category=ToolCategory.FILE_READ,
            keywords=[
                "读取文件",
                "打开文件",
                "查看文件",
                "显示文件",
                "读文件",
                "cat",
                "看文件内容",
                "read file",
                "show content",
            ],
            param_patterns={
                "path": r"(?:读取|打开|查看|显示|读|cat|看|read|show)\s+[`'\"](.+)[`'\"]|\s+([^\s]+\.\w+)"
            },
            description="读取文件内容",
        )

        self.tools["write_file"] = ToolConfig(
            name="write_file",
            category=ToolCategory.FILE_WRITE,
            keywords=["写入文件", "保存文件", "创建文件", "写文件", "新建文件", "新建"],
            param_patterns={
                "path": r"(?:写入|保存|创建|写|新建)\s+[`'\"](.+)[`'\"]",
                "content": r"内容[为:：]\s*(.+)$",
            },
            description="写入或创建文件",
        )

        self.tools["edit_file"] = ToolConfig(
            name="edit_file",
            category=ToolCategory.FILE_EDIT,
            keywords=["编辑文件", "修改文件", "改动文件", "改文件"],
            param_patterns={
                "path": r"(?:编辑|修改|改动|改)\s+[`'\"](.+)[`'\"]",
                "oldString": r"把\s*(.+?)\s+改成",
                "newString": r"改成\s*(.+)$",
            },
            description="编辑文件",
        )

        self.tools["delete_file"] = ToolConfig(
            name="delete_file",
            category=ToolCategory.FILE_DELETE,
            keywords=["删除文件", "移除文件", "删掉", "删除", "去掉文件"],
            param_patterns={"path": r"(?:删除|移除|删掉|去掉)\s+[`'\"](.+)[`'\"]"},
            requires_confirmation=True,
            description="删除文件",
        )

        self.tools["grep"] = ToolConfig(
            name="grep",
            category=ToolCategory.FILE_SEARCH,
            keywords=["搜索内容", "查找内容", "搜索代码", "找内容", "grep"],
            param_patterns={
                "pattern": r"(?:搜索|查找|找|grep)\s+[`'\"](.+)[`'\"]",
                "path": r"在\s+([^/\s]+)",
            },
            description="搜索文件内容",
        )

        self.tools["glob"] = ToolConfig(
            name="glob",
            category=ToolCategory.FILE_SEARCH,
            keywords=["搜索文件", "查找文件", "找文件", "列出文件", "文件列表"],
            param_patterns={
                "pattern": r"(?:搜索|查找|找|列出)\s+(.+?)(?:\s+文件|$)",
                "path": r"在\s+(.+?)\s+(?:目录|文件夹)",
            },
            description="搜索文件",
        )

        self.tools["list_directory"] = ToolConfig(
            name="list_directory",
            category=ToolCategory.FILE_LIST,
            keywords=["列出目录", "查看目录", "目录内容", "文件夹内容", "ls", "dir", "列表"],
            param_patterns={"path": r"(?:列出|查看)\s*(.*?)(?:目录|文件夹|的内容)?$"},
            description="列出目录内容",
        )

        self.tools["run_shell"] = ToolConfig(
            name="run_shell",
            category=ToolCategory.SYSTEM_COMMAND,
            keywords=["执行命令", "运行命令", "执行脚本", "跑命令", "终端命令", "cmd", "shell"],
            param_patterns={"command": r"(?:执行|运行|跑)\s*(.+?)(?:命令|脚本)?$"},
            requires_confirmation=True,
            description="执行系统命令",
        )

        self.tools["browser_open"] = ToolConfig(
            name="browser_open",
            category=ToolCategory.BROWSER,
            keywords=["打开浏览器", "浏览", "访问网页", "打开网页"],
            param_patterns={
                "url": r"(?:打开浏览器|浏览|访问|打开)\s+(https?://[^\s]+|[a-z]+\.[a-z]+)"
            },
            description="打开浏览器",
        )

        self.tools["browser_navigate"] = ToolConfig(
            name="browser_navigate",
            category=ToolCategory.BROWSER,
            keywords=["导航到", "跳转", "去往", "访问"],
            param_patterns={"url": r"(?:导航到|跳转|去往|访问)\s+(https?://[^\s]+|[a-z]+\.[a-z]+)"},
            description="浏览器导航",
        )

        self.tools["browser_click"] = ToolConfig(
            name="browser_click",
            category=ToolCategory.BROWSER,
            keywords=["点击", "按下", "选择"],
            param_patterns={"selector": r"点击\s+(.+)"},
            description="点击页面元素",
        )

        self.tools["browser_screenshot"] = ToolConfig(
            name="browser_screenshot",
            category=ToolCategory.BROWSER,
            keywords=["截图", "截屏", "屏幕截图", "截图看看"],
            param_patterns={},
            description="浏览器截图",
        )

        self.tools["web_search"] = ToolConfig(
            name="web_search",
            category=ToolCategory.WEB_SEARCH,
            keywords=[
                "搜索",
                "查找",
                "查一下",
                "百度",
                "google",
                "搜一下",
                "查资料",
                "问一下",
                "search",
                "find",
                "lookup",
            ],
            param_patterns={
                "query": r"(?:搜索|查找|查一下|百度|google|搜|问|search|find)\s+(.+?)(?:[?。.]|$)"
            },
            description="网络搜索",
        )

        self.tools["web_fetch"] = ToolConfig(
            name="web_fetch",
            category=ToolCategory.WEB_SCRAPE,
            keywords=["获取网页", "抓取网页", "爬取", "下载网页"],
            param_patterns={"url": r"(?:获取|抓取|爬取|下载)\s+(https?://[^\s]+)"},
            description="获取网页内容",
        )

        self.tools["download_file"] = ToolConfig(
            name="download_file",
            category=ToolCategory.DOWNLOAD,
            keywords=["下载", "下载文件", "获取文件", "保存到本地"],
            param_patterns={"url": r"下载\s+(https?://[^\s]+)", "filename": r"保存为\s+(.+)"},
            description="下载文件",
        )

        self.tools["system_info"] = ToolConfig(
            name="get_system_info",
            category=ToolCategory.SYSTEM_INFO,
            keywords=["系统信息", "电脑配置", "系统版本", "操作系统", "内存", "cpu", "配置"],
            param_patterns={},
            description="获取系统信息",
        )

        self.tools["calculator"] = ToolConfig(
            name="calculator",
            category=ToolCategory.CALCULATOR,
            keywords=[
                "计算",
                "等于",
                "加减乘除",
                "数学",
                "运算",
                "多少",
                "算一下",
                "calculate",
                "compute",
                "what is",
            ],
            param_patterns={
                "expression": r"(?:计算|等于|算一下|calculate|compute|what is)\s+(.+?)(?:[?。.]|$)"
            },
            description="数学计算",
        )

        self.tools["get_date_time"] = ToolConfig(
            name="get_date_time",
            category=ToolCategory.DATE_TIME,
            keywords=[
                "现在几点",
                "当前时间",
                "今天日期",
                "几点了",
                "星期几",
                "日期",
                "时间",
                "what time",
                "current time",
                "date today",
            ],
            param_patterns={"timezone": r"(?:时间|日期|time|date)\s+(?:in\s+)?(.+)"},
            description="获取日期时间",
        )

        self.tools["unit_convert"] = ToolConfig(
            name="unit_convert",
            category=ToolCategory.UNIT_CONVERT,
            keywords=["转换单位", "单位换算", "转成", "换算", "多少"],
            param_patterns={"value": r"(\d+(?:\.\d+)?)\s*(\w+)\s*转成?\s*(\w+)"},
            description="单位转换",
        )

        self.tools["summarize"] = ToolConfig(
            name="summarize",
            category=ToolCategory.TEXT_SUMMARIZE,
            keywords=["总结", "摘要", "概括", "归纳", "简而言之", "总结一下"],
            param_patterns={
                "text": r"(?:总结|摘要|概括|归纳|简而言之|总结一下)\s+(.+?)(?:[？。]|$)"
            },
            description="文本摘要",
        )

        self.tools["translate"] = ToolConfig(
            name="translate",
            category=ToolCategory.TEXT_TRANSLATE,
            keywords=["翻译", "译成", "translate", "英文翻译", "中文翻译", "翻译成"],
            param_patterns={
                "text": r"(?:翻译|译成|翻译成)\s+(.+?)(?:[？。]|$)",
                "target_lang": r"译成\s+(\w+)",
            },
            description="文本翻译",
        )

        self.tools["generate_image"] = ToolConfig(
            name="generate_image",
            category=ToolCategory.IMAGE_GENERATE,
            keywords=["生成图片", "画图", "创建图像", "AI绘画", "画一张", "画个"],
            param_patterns={"prompt": r"(?:生成|画|创建)\s+(.+?)(?:图片|图像|画|$)"},
            description="生成图片",
        )

        self.tools["send_email"] = ToolConfig(
            name="send_email",
            category=ToolCategory.SEND_EMAIL,
            keywords=["发送邮件", "发邮件", "email", "邮件发送"],
            param_patterns={
                "to": r"发给\s+([^\s]+@[^\s]+)",
                "subject": r"主题[为:：]\s*(.+)",
                "body": r"内容[为:：]\s*(.+)",
            },
            requires_confirmation=True,
            description="发送邮件",
        )

        self.tools["schedule_task"] = ToolConfig(
            name="schedule_task",
            category=ToolCategory.REMINDER,
            keywords=["提醒", "闹钟", "记一下", "待办", "别忘了", "定时"],
            param_patterns={
                "content": r"(?:提醒|记一下|待办|定时)\s+(.+?)(?:在|$)",
                "time": r"在\s+(.+?)(?:提醒|时)",
            },
            description="设置提醒",
        )

        self.tools["weather"] = ToolConfig(
            name="weather",
            category=ToolCategory.WEATHER,
            keywords=["天气", "气温", "下雨", "晴天", "阴天", "天气预报"],
            param_patterns={"city": r"(?:天气|气温)\s+in\s+(.+?)(?:[？。]|$)|天气\s*(.{2,6})"},
            description="查询天气",
        )

        self.tools["news"] = ToolConfig(
            name="news",
            category=ToolCategory.NEWS,
            keywords=["新闻", "资讯", "热点", "头条"],
            param_patterns={"topic": r"(?:新闻|资讯|热点|头条)\s+(.+?)(?:[？。]|$)"},
            description="查询新闻",
        )

        self.tools["delegate_to_agent"] = ToolConfig(
            name="delegate_to_agent",
            category=ToolCategory.CODE_RUN,
            keywords=["委托", "代理", "调用其他", "分配任务"],
            param_patterns={"agent": r"委托\s+(\w+)", "task": r"做\s+(.+)$"},
            description="委托子代理",
        )

        self.tools["run_code"] = ToolConfig(
            name="run_code",
            category=ToolCategory.CODE_RUN,
            keywords=["运行代码", "执行代码", "跑代码", "运行脚本", "执行脚本", "执行"],
            param_patterns={
                "code": r"(?:运行|执行|跑)\s*[`'\"](.+)[`'\"]",
                "language": r"用\s+(\w+)\s+运行",
            },
            description="运行代码",
        )

        self.tools["todo_handler"] = ToolConfig(
            name="todo_handler",
            category=ToolCategory.REMINDER,
            keywords=["待办", "todo", "任务", "记下来"],
            param_patterns={
                "content": r"(?:待办|todo|任务|记下来)\s+(.+?)(?:在|$)",
                "action": r"(?:添加|创建|完成|删除)",
            },
            description="待办事项",
        )

    def route(self, text: str) -> tuple[str, dict] | None:
        """路由到工具"""
        text_lower = text.lower()

        scores = {}
        matched_params = {}

        for tool_name, tool in self.tools.items():
            score = 0
            matched = []

            for keyword in tool.keywords:
                if keyword in text_lower:
                    score += 1
                    matched.append(keyword)

            if score == 0:
                continue

            if len(matched) > 1:
                score += len(matched) * 0.5

            params = self._extract_params(text, tool)
            if params:
                score += 1

            scores[tool_name] = score
            matched_params[tool_name] = params

        if not scores:
            return None

        best_tool = max(scores, key=scores.get)
        best_score = scores[best_tool]

        if best_score < 1:
            return None

        return (best_tool, matched_params.get(best_tool, {}))

    def _extract_params(self, text: str, tool: ToolConfig) -> dict:
        """提取工具参数"""
        params = {}

        for param_name, pattern in tool.param_patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                for group in match.groups():
                    if group:
                        params[param_name] = group.strip()
                        break

        return params

    def get_tool(self, name: str) -> ToolConfig | None:
        """获取工具配置"""
        return self.tools.get(name)

    def add_tool(self, name: str, config: ToolConfig):
        """动态添加工具"""
        self.tools[name] = config
