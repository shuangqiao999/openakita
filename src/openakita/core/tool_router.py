"""
智能工具路由器 - 增强版
支持: 关键词匹配、否定词过滤、优先级、参数提取、模糊匹配
"""

import re
import random
import base64
import json
import subprocess
import platform
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime

# ============================================================
# 枚举定义
# ============================================================


class ToolCategory(Enum):
    """工具类别"""

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

    # 新增类别
    SESSION = "session"  # 会话管理
    AGENT = "agent"  # Agent 控制
    CLIPBOARD = "clipboard"  # 剪贴板
    UTILITY = "utility"  # 实用工具
    MEDIA = "media"  # 媒体操作
    DEV = "dev"  # 开发辅助


# ============================================================
# 数据类定义
# ============================================================


@dataclass
class ToolConfig:
    """工具配置"""

    name: str
    category: ToolCategory
    keywords: List[str]
    param_patterns: Dict[str, str]
    description: str = ""
    requires_confirmation: bool = False
    priority: int = 0  # 优先级，数字越大优先级越高
    negative_keywords: List[str] = field(default_factory=list)  # 否定词


# ============================================================
# 智能工具路由器
# ============================================================


class ToolRouter:
    """智能工具路由器 - 增强版"""

    def __init__(self):
        self.tools: Dict[str, ToolConfig] = {}
        self._init_default_tools()
        self._init_enhanced_tools()

    # ============================================================
    # 初始化工具
    # ============================================================

    def _init_default_tools(self):
        """初始化默认工具"""

        # ----- 文件操作 -----
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
                "read",
                "显示",
                "看",
            ],
            param_patterns={"path": r"(?:读取|打开|查看|显示|读|cat|看)\s*[`'\" ]*([^\s`'\"]+)"},
            description="读取文件内容",
            priority=5,
            negative_keywords=["不要", "别读", "不显示"],
        )

        self.tools["write_file"] = ToolConfig(
            name="write_file",
            category=ToolCategory.FILE_WRITE,
            keywords=["写入文件", "保存文件", "创建文件", "写文件", "新建文件", "新建"],
            param_patterns={
                "path": r"(?:写入|保存|创建|写|新建)\s*[`'\" ]*([^\s`'\"]+)",
                "content": r"内容[为:：]\s*(.+)$",
            },
            description="写入或创建文件",
            priority=5,
        )

        self.tools["edit_file"] = ToolConfig(
            name="edit_file",
            category=ToolCategory.FILE_EDIT,
            keywords=["编辑文件", "修改文件", "改动文件", "改文件"],
            param_patterns={
                "path": r"(?:编辑|修改|改动|改)\s*[`'\" ]*([^\s`'\"]+)",
                "old_string": r"把\s*(.+?)\s+改成",
                "new_string": r"改成\s*(.+)$",
            },
            description="编辑文件",
            priority=4,
        )

        self.tools["delete_file"] = ToolConfig(
            name="delete_file",
            category=ToolCategory.FILE_DELETE,
            keywords=["删除文件", "移除文件", "删掉", "删除", "去掉文件"],
            param_patterns={"path": r"(?:删除|移除|删掉|去掉)\s*[`'\" ]*([^\s`'\"]+)"},
            description="删除文件",
            requires_confirmation=True,
            priority=4,
            negative_keywords=["不要删", "别删", "恢复"],
        )

        self.tools["grep"] = ToolConfig(
            name="grep",
            category=ToolCategory.FILE_SEARCH,
            keywords=["搜索内容", "查找内容", "搜索代码", "找内容", "grep"],
            param_patterns={
                "pattern": r"(?:搜索|查找|找|grep)\s*[`'\" ]*([^\s`'\"]+)",
                "path": r"在\s+([^/\s]+)",
            },
            description="搜索文件内容",
            priority=4,
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
            priority=4,
        )

        self.tools["list_directory"] = ToolConfig(
            name="list_directory",
            category=ToolCategory.FILE_LIST,
            keywords=["列出目录", "查看目录", "目录内容", "文件夹内容", "ls", "dir", "列表"],
            param_patterns={"path": r"(?:列出|查看)\s*(.*?)(?:目录|文件夹|的内容)?$"},
            description="列出目录内容",
            priority=4,
        )

        # ----- 代码操作 -----
        self.tools["run_code"] = ToolConfig(
            name="run_code",
            category=ToolCategory.CODE_RUN,
            keywords=["运行代码", "执行代码", "跑代码", "运行脚本", "执行脚本", "执行"],
            param_patterns={
                "code": r"(?:运行|执行|跑)\s*[`'\"](.+)[`'\"]",
                "language": r"用\s+(\w+)\s+运行",
            },
            description="运行代码",
            priority=3,
        )

        # ----- 浏览器操作 -----
        self.tools["browser_open"] = ToolConfig(
            name="browser_open",
            category=ToolCategory.BROWSER,
            keywords=["打开浏览器", "浏览", "访问网页", "打开网页"],
            param_patterns={
                "url": r"(?:打开浏览器|浏览|访问|打开)\s+(https?://[^\s]+|[a-z]+\.[a-z]+)"
            },
            description="打开浏览器",
            priority=4,
        )

        self.tools["browser_navigate"] = ToolConfig(
            name="browser_navigate",
            category=ToolCategory.BROWSER,
            keywords=["导航到", "跳转", "去往", "访问"],
            param_patterns={"url": r"(?:导航到|跳转|去往|访问)\s+(https?://[^\s]+|[a-z]+\.[a-z]+)"},
            description="浏览器导航",
            priority=4,
        )

        self.tools["browser_click"] = ToolConfig(
            name="browser_click",
            category=ToolCategory.BROWSER,
            keywords=["点击", "按下", "选择"],
            param_patterns={"selector": r"点击\s+(.+)"},
            description="点击页面元素",
            priority=3,
        )

        self.tools["browser_screenshot"] = ToolConfig(
            name="browser_screenshot",
            category=ToolCategory.BROWSER,
            keywords=["截图", "截屏", "屏幕截图", "截图看看"],
            param_patterns={},
            description="浏览器截图",
            priority=3,
        )

        # ----- 网络搜索 -----
        self.tools["web_search"] = ToolConfig(
            name="web_search",
            category=ToolCategory.WEB_SEARCH,
            keywords=["搜索", "查找", "查一下", "百度", "google", "搜一下", "查资料", "问一下"],
            param_patterns={"query": r"(?:搜索|查找|查一下|百度|google|搜|问)\s+(.+?)(?:[?。.]|$)"},
            description="网络搜索",
            priority=6,
            negative_keywords=["不要搜", "别搜"],
        )

        self.tools["web_fetch"] = ToolConfig(
            name="web_fetch",
            category=ToolCategory.WEB_SCRAPE,
            keywords=["获取网页", "抓取网页", "爬取", "下载网页"],
            param_patterns={"url": r"(?:获取|抓取|爬取|下载)\s+(https?://[^\s]+)"},
            description="获取网页内容",
            priority=3,
        )

        self.tools["download_file"] = ToolConfig(
            name="download_file",
            category=ToolCategory.DOWNLOAD,
            keywords=["下载", "下载文件", "获取文件", "保存到本地"],
            param_patterns={"url": r"下载\s+(https?://[^\s]+)", "filename": r"保存为\s+(.+)"},
            description="下载文件",
            priority=3,
        )

        # ----- 系统操作 -----
        self.tools["run_shell"] = ToolConfig(
            name="run_shell",
            category=ToolCategory.SYSTEM_COMMAND,
            keywords=["执行命令", "运行命令", "执行脚本", "跑命令", "终端命令", "cmd", "shell"],
            param_patterns={"command": r"(?:执行|运行|跑)\s*(.+?)(?:命令|脚本)?$"},
            description="执行系统命令",
            requires_confirmation=True,
            priority=3,
            negative_keywords=["不要执行", "别跑"],
        )

        self.tools["system_info"] = ToolConfig(
            name="get_system_info",
            category=ToolCategory.SYSTEM_INFO,
            keywords=["系统信息", "电脑配置", "系统版本", "操作系统", "内存", "cpu", "配置"],
            param_patterns={},
            description="获取系统信息",
            priority=5,
        )

        # ----- 计算/时间 -----
        self.tools["calculator"] = ToolConfig(
            name="calculator",
            category=ToolCategory.CALCULATOR,
            keywords=["计算", "等于", "加减乘除", "数学", "运算", "多少", "算一下"],
            param_patterns={"expression": r"(?:计算|等于|算一下)\s+(.+?)(?:[?。.]|$)"},
            description="数学计算",
            priority=7,
        )

        self.tools["get_date_time"] = ToolConfig(
            name="get_date_time",
            category=ToolCategory.DATE_TIME,
            keywords=["现在几点", "当前时间", "今天日期", "几点了", "星期几", "日期", "时间"],
            param_patterns={"timezone": r"(?:时间|日期)\s+(?:in\s+)?(.+)"},
            description="获取日期时间",
            priority=8,
        )

        self.tools["unit_convert"] = ToolConfig(
            name="unit_convert",
            category=ToolCategory.UNIT_CONVERT,
            keywords=["转换单位", "单位换算", "转成", "换算", "多少"],
            param_patterns={"value": r"(\d+(?:\.\d+)?)\s*(\w+)\s*转成?\s*(\w+)"},
            description="单位转换",
            priority=3,
        )

        # ----- 文本处理 -----
        self.tools["summarize"] = ToolConfig(
            name="summarize",
            category=ToolCategory.TEXT_SUMMARIZE,
            keywords=["总结", "摘要", "概括", "归纳", "简而言之", "总结一下"],
            param_patterns={
                "text": r"(?:总结|摘要|概括|归纳|简而言之|总结一下)\s+(.+?)(?:[？。]|$)"
            },
            description="文本摘要",
            priority=3,
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
            priority=5,
        )

        # ----- 图像 -----
        self.tools["generate_image"] = ToolConfig(
            name="generate_image",
            category=ToolCategory.IMAGE_GENERATE,
            keywords=["生成图片", "画图", "创建图像", "AI绘画", "画一张", "画个"],
            param_patterns={"prompt": r"(?:生成|画|创建)\s+(.+?)(?:图片|图像|画|$)"},
            description="生成图片",
            priority=3,
        )

        # ----- 通信 -----
        self.tools["send_email"] = ToolConfig(
            name="send_email",
            category=ToolCategory.SEND_EMAIL,
            keywords=["发送邮件", "发邮件", "email", "邮件发送"],
            param_patterns={
                "to": r"发给\s+([^\s]+@[^\s]+)",
                "subject": r"主题[为:：]\s*(.+)",
                "body": r"内容[为:：]\s*(.+)",
            },
            description="发送邮件",
            requires_confirmation=True,
            priority=2,
        )

        # ----- 提醒 -----
        self.tools["schedule_task"] = ToolConfig(
            name="schedule_task",
            category=ToolCategory.REMINDER,
            keywords=["提醒", "闹钟", "记一下", "待办", "别忘了", "定时"],
            param_patterns={
                "content": r"(?:提醒|记一下|待办|定时)\s+(.+?)(?:在|$)",
                "time": r"在\s+(.+?)(?:提醒|时)",
            },
            description="设置提醒",
            priority=4,
        )

        # ----- 信息查询 -----
        self.tools["weather"] = ToolConfig(
            name="weather",
            category=ToolCategory.WEATHER,
            keywords=["天气", "气温", "下雨", "晴天", "阴天", "天气预报"],
            param_patterns={"city": r"(?:天气|气温)\s+in\s+(.+?)(?:[？。]|$)|天气\s*(.{2,6})"},
            description="查询天气",
            priority=3,
        )

        self.tools["news"] = ToolConfig(
            name="news",
            category=ToolCategory.NEWS,
            keywords=["新闻", "资讯", "热点", "头条"],
            param_patterns={"topic": r"(?:新闻|资讯|热点|头条)\s+(.+?)(?:[？。]|$)"},
            description="查询新闻",
            priority=3,
        )

    def _init_enhanced_tools(self):
        """初始化增强工具（新增）"""

        # ============================================================
        # 会话管理
        # ============================================================

        self.tools["get_memory"] = ToolConfig(
            name="get_memory",
            category=ToolCategory.SESSION,
            keywords=[
                "还记得",
                "之前说过",
                "回忆",
                "查一下记忆",
                "我上次说",
                "记不记得",
                "还记不记得",
            ],
            param_patterns={
                "query": r"(?:还记得|之前说过|回忆|查一下|记不记得)\s*(.+?)(?:[？。]|$)"
            },
            description="查询历史记忆",
            priority=6,
        )

        self.tools["clear_session"] = ToolConfig(
            name="clear_session",
            category=ToolCategory.SESSION,
            keywords=[
                "清空对话",
                "重置对话",
                "新对话",
                "重新开始",
                "清除历史",
                "清空聊天",
                "重置会话",
            ],
            param_patterns={},
            description="清空当前会话",
            requires_confirmation=True,
            priority=5,
        )

        self.tools["export_chat"] = ToolConfig(
            name="export_chat",
            category=ToolCategory.SESSION,
            keywords=["导出对话", "保存聊天记录", "导出记录", "下载对话", "导出聊天"],
            param_patterns={"format": r"导出为\s*(\w+)", "path": r"保存到\s+(.+)"},
            description="导出对话记录",
            priority=3,
        )

        self.tools["undo_last"] = ToolConfig(
            name="undo_last",
            category=ToolCategory.SESSION,
            keywords=["撤销", "回退", "上一步", "取消", "撤回", "undo", "后退"],
            param_patterns={},
            description="撤销上一条操作",
            priority=6,
        )

        # ============================================================
        # Agent 控制
        # ============================================================

        self.tools["restart_agent"] = ToolConfig(
            name="restart_agent",
            category=ToolCategory.AGENT,
            keywords=["重启", "重启服务", "重新加载", "刷新", "重启一下"],
            param_patterns={},
            description="重启 Agent",
            requires_confirmation=True,
            priority=4,
        )

        self.tools["switch_persona"] = ToolConfig(
            name="switch_persona",
            category=ToolCategory.AGENT,
            keywords=["切换角色", "变成", "扮演", "人格切换", "作为", "切换到", "变身为"],
            param_patterns={
                "persona": r"(?:切换|变成|扮演|作为|切换到|变身为)\s+(.+?)(?:[？。]|$)"
            },
            description="切换人格模式",
            priority=6,
        )

        self.tools["toggle_proactive"] = ToolConfig(
            name="toggle_proactive",
            category=ToolCategory.AGENT,
            keywords=["开启主动", "关闭主动", "活人感", "主动模式", "自动回复"],
            param_patterns={"enabled": r"(开启|关闭)\s*主动"},
            description="开关活人感模式",
            priority=5,
        )

        self.tools["get_status"] = ToolConfig(
            name="get_status",
            category=ToolCategory.AGENT,
            keywords=["状态", "运行状态", "健康检查", "什么状态", "怎么样了", "在干嘛"],
            param_patterns={},
            description="获取 Agent 状态",
            priority=7,
        )

        # ============================================================
        # 剪贴板操作
        # ============================================================

        self.tools["clipboard_read"] = ToolConfig(
            name="clipboard_read",
            category=ToolCategory.CLIPBOARD,
            keywords=["剪贴板", "粘贴板", "复制的内容", "剪贴板内容", "看看剪贴板"],
            param_patterns={},
            description="读取剪贴板内容",
            priority=5,
        )

        self.tools["clipboard_write"] = ToolConfig(
            name="clipboard_write",
            category=ToolCategory.CLIPBOARD,
            keywords=["复制", "拷贝", "复制到剪贴板", "复制文本"],
            param_patterns={"content": r"(?:复制|拷贝)\s+(.+?)(?:[？。]|$)"},
            description="写入剪贴板",
            priority=5,
        )

        # ============================================================
        # 实用工具
        # ============================================================

        self.tools["random_number"] = ToolConfig(
            name="random_number",
            category=ToolCategory.UTILITY,
            keywords=["随机数", "随机数字", "抽一个", "随机", "摇号", "随机抽取"],
            param_patterns={"min": r"从\s*(\d+)", "max": r"到\s*(\d+)"},
            description="生成随机数",
            priority=6,
        )

        self.tools["password_generate"] = ToolConfig(
            name="password_generate",
            category=ToolCategory.UTILITY,
            keywords=["生成密码", "随机密码", "密码生成", "强密码", "password", "生成一个密码"],
            param_patterns={"length": r"(\d+)\s*位", "complexity": r"(简单|中等|复杂)"},
            description="生成随机密码",
            priority=4,
        )

        self.tools["qr_generate"] = ToolConfig(
            name="qr_generate",
            category=ToolCategory.UTILITY,
            keywords=["生成二维码", "二维码", "制作二维码", "qr码"],
            param_patterns={"content": r"(?:生成|制作)\s+(.+?)(?:二维码|的二维码|$)"},
            description="生成二维码",
            priority=3,
        )

        self.tools["shorten_url"] = ToolConfig(
            name="shorten_url",
            category=ToolCategory.UTILITY,
            keywords=["缩短链接", "短链接", "链接缩短"],
            param_patterns={"url": r"缩短\s+(https?://[^\s]+)"},
            description="URL 缩短",
            priority=2,
        )

        # ============================================================
        # 开发辅助
        # ============================================================

        self.tools["format_code"] = ToolConfig(
            name="format_code",
            category=ToolCategory.DEV,
            keywords=["格式化代码", "代码美化", "代码格式化", "美化代码"],
            param_patterns={
                "code": r"(?:格式化|美化)\s*[`'\"](.+)[`'\"]",
                "language": r"用\s+(\w+)\s+格式化",
            },
            description="代码格式化",
            priority=3,
        )

        self.tools["json_validate"] = ToolConfig(
            name="json_validate",
            category=ToolCategory.DEV,
            keywords=["验证JSON", "JSON格式", "检查JSON", "json验证"],
            param_patterns={"json": r"(?:验证|检查)\s*([{[]).+[}\]]"},
            description="JSON 验证",
            priority=4,
        )

        self.tools["timestamp_convert"] = ToolConfig(
            name="timestamp_convert",
            category=ToolCategory.DEV,
            keywords=["时间戳转换", "时间戳", "转时间戳", "时间戳转日期"],
            param_patterns={"timestamp": r"(\d{10,13})", "direction": r"(转日期|转时间戳)"},
            description="时间戳转换",
            priority=4,
        )

        self.tools["base64_encode"] = ToolConfig(
            name="base64_encode",
            category=ToolCategory.DEV,
            keywords=["base64编码", "转base64", "base64加密"],
            param_patterns={"text": r"(?:base64编码|转base64)\s+(.+?)(?:[？。]|$)"},
            description="Base64 编码",
            priority=4,
        )

        self.tools["base64_decode"] = ToolConfig(
            name="base64_decode",
            category=ToolCategory.DEV,
            keywords=["base64解码", "解base64", "base64解密"],
            param_patterns={"data": r"(?:base64解码|解base64)\s+(.+?)(?:[？。]|$)"},
            description="Base64 解码",
            priority=4,
        )

        # ============================================================
        # 媒体操作
        # ============================================================

        self.tools["take_photo"] = ToolConfig(
            name="take_photo",
            category=ToolCategory.MEDIA,
            keywords=["拍照", "照相", "拍一张", "拍照片"],
            param_patterns={},
            description="拍照",
            priority=3,
        )

        self.tools["play_audio"] = ToolConfig(
            name="play_audio",
            category=ToolCategory.MEDIA,
            keywords=["播放", "播放音乐", "放首歌", "播放音频"],
            param_patterns={"file": r"(?:播放|放)\s+(.+?)(?:音乐|音频|歌曲|$)"},
            description="播放音频",
            priority=2,
        )

        # ============================================================
        # 信息查询增强
        # ============================================================

        self.tools["get_ip"] = ToolConfig(
            name="get_ip",
            category=ToolCategory.SYSTEM_INFO,
            keywords=["IP地址", "本机IP", "公网IP", "我的IP"],
            param_patterns={},
            description="获取 IP 地址",
            priority=4,
        )

        self.tools["get_location"] = ToolConfig(
            name="get_location",
            category=ToolCategory.SYSTEM_INFO,
            keywords=["定位", "当前位置", "我在哪", "位置"],
            param_patterns={},
            description="获取地理位置",
            priority=3,
        )

        self.tools["get_battery"] = ToolConfig(
            name="get_battery",
            category=ToolCategory.SYSTEM_INFO,
            keywords=["电量", "剩余电量", "电池", "电池电量"],
            param_patterns={},
            description="获取电池信息",
            priority=4,
        )

    # ============================================================
    # 核心路由方法
    # ============================================================

    def route(self, text: str) -> Optional[Tuple[str, Dict]]:
        """
        路由到工具

        Args:
            text: 用户输入文本

        Returns:
            (tool_name, params) 或 None
        """
        text_lower = text.lower()

        # 1. 否定词快速过滤
        for tool in self.tools.values():
            for neg in tool.negative_keywords:
                if neg in text_lower:
                    return None

        # 2. 计算每个工具的得分
        scores = {}
        matched_params = {}

        for tool_name, tool in self.tools.items():
            score = 0
            matched = []

            # 关键词匹配
            for keyword in tool.keywords:
                if keyword in text_lower:
                    score += 1
                    matched.append(keyword)

            if score == 0:
                continue

            # 多关键词加分
            if len(matched) > 1:
                score += len(matched) * 0.5

            # 参数提取加分
            params = self._extract_params(text, tool)
            if params:
                score += 1.5  # 能提取到参数说明意图明确

            # 优先级加权
            score += tool.priority * 0.1

            scores[tool_name] = score
            matched_params[tool_name] = params

        if not scores:
            return None

        # 3. 选择最高分工具
        best_tool = max(scores, key=scores.get)
        best_score = scores[best_tool]

        # 4. 阈值过滤
        if best_score < 1.5:
            return None

        return (best_tool, matched_params.get(best_tool, {}))

    def _extract_params(self, text: str, tool: ToolConfig) -> Dict:
        """提取工具参数"""
        params = {}

        for param_name, pattern in tool.param_patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                # 获取第一个非空捕获组
                for group in match.groups():
                    if group:
                        params[param_name] = group.strip()
                        break

        return params

    def get_tool(self, name: str) -> Optional[ToolConfig]:
        """获取工具配置"""
        return self.tools.get(name)

    def add_tool(self, name: str, config: ToolConfig):
        """动态添加工具"""
        self.tools[name] = config

    def list_tools(self, category: Optional[ToolCategory] = None) -> List[str]:
        """列出工具"""
        if category:
            return [t.name for t in self.tools.values() if t.category == category]
        return list(self.tools.keys())


# ============================================================
# 工具执行器
# ============================================================


class ToolExecutor:
    """工具执行器 - 补充无 Skill 的工具"""

    def __init__(self, agent=None, skill_executor=None):
        """
        Args:
            agent: Agent 实例（用于访问内部方法）
            skill_executor: Skill 执行器（用于调用已有 Skill）
        """
        self.agent = agent
        self.skill_executor = skill_executor

    async def execute(self, tool_name: str, params: Dict[str, Any]) -> str:
        """
        执行工具

        Args:
            tool_name: 工具名称
            params: 参数字典

        Returns:
            执行结果字符串
        """
        # ============================================================
        # 1. 优先使用已有 Skill
        # ============================================================
        if self.skill_executor:
            try:
                result = await self.skill_executor.execute(tool_name, params)
                if result:
                    return result
            except Exception as e:
                # Skill 不存在或执行失败，继续尝试内嵌实现
                pass

        # ============================================================
        # 2. 会话管理
        # ============================================================
        if tool_name == "get_memory":
            return await self._get_memory(params)

        if tool_name == "clear_session":
            return await self._clear_session()

        if tool_name == "export_chat":
            return await self._export_chat(params)

        if tool_name == "undo_last":
            return await self._undo_last()

        # ============================================================
        # 3. Agent 控制
        # ============================================================
        if tool_name == "restart_agent":
            return await self._restart_agent()

        if tool_name == "switch_persona":
            return await self._switch_persona(params)

        if tool_name == "toggle_proactive":
            return await self._toggle_proactive(params)

        if tool_name == "get_status":
            return await self._get_status()

        # ============================================================
        # 4. 剪贴板操作
        # ============================================================
        if tool_name == "clipboard_read":
            return self._clipboard_read()

        if tool_name == "clipboard_write":
            return self._clipboard_write(params)

        # ============================================================
        # 5. 实用工具（纯计算，无依赖）
        # ============================================================
        if tool_name == "random_number":
            return self._random_number(params)

        if tool_name == "password_generate":
            return self._password_generate(params)

        if tool_name == "qr_generate":
            return await self._qr_generate(params)

        if tool_name == "shorten_url":
            return await self._shorten_url(params)

        # ============================================================
        # 6. 开发辅助
        # ============================================================
        if tool_name == "format_code":
            return self._format_code(params)

        if tool_name == "json_validate":
            return self._json_validate(params)

        if tool_name == "timestamp_convert":
            return self._timestamp_convert(params)

        if tool_name == "base64_encode":
            return self._base64_encode(params)

        if tool_name == "base64_decode":
            return self._base64_decode(params)

        # ============================================================
        # 7. 媒体操作
        # ============================================================
        if tool_name == "take_photo":
            return await self._take_photo()

        if tool_name == "play_audio":
            return await self._play_audio(params)

        # ============================================================
        # 8. 信息查询
        # ============================================================
        if tool_name == "get_ip":
            return self._get_ip()

        if tool_name == "get_location":
            return await self._get_location()

        if tool_name == "get_battery":
            return self._get_battery()

        # ============================================================
        # 9. 未知工具
        # ============================================================
        return f"⚠️ 工具 '{tool_name}' 暂未实现。如需使用，请安装对应的 Skill。"

    # ============================================================
    # 内嵌实现
    # ============================================================

    # ----- 会话管理 -----
    async def _get_memory(self, params: dict) -> str:
        """查询历史记忆"""
        query = params.get("query", "")
        if not query:
            return "你想查询什么？请告诉我具体内容。"

        if self.agent and hasattr(self.agent, "search_memory"):
            result = await self.agent.search_memory(query)
            return result if result else f"没有找到关于「{query}」的记忆。"

        return "记忆功能需要启用 Memory Manager。"

    async def _clear_session(self) -> str:
        """清空会话"""
        if self.agent and hasattr(self.agent, "clear_session"):
            await self.agent.clear_session()
            return "✅ 会话已清空，我们可以重新开始了。"
        return "⚠️ 无法清空会话，请手动刷新。"

    async def _export_chat(self, params: dict) -> str:
        """导出对话"""
        format_type = params.get("format", "txt")
        path = params.get("path", "")

        if self.agent and hasattr(self.agent, "export_chat"):
            result = await self.agent.export_chat(format_type, path)
            return result
        return "⚠️ 导出功能暂不可用。"

    async def _undo_last(self) -> str:
        """撤销上一条"""
        if self.agent and hasattr(self.agent, "undo_last"):
            await self.agent.undo_last()
            return "✅ 已撤销上一条操作。"
        return "⚠️ 撤销功能暂不可用。"

    # ----- Agent 控制 -----
    async def _restart_agent(self) -> str:
        """重启 Agent"""
        # 注意：实际重启需要外部处理
        return "⚠️ 重启功能需要外部支持，请手动重启服务。"

    async def _switch_persona(self, params: dict) -> str:
        """切换人格"""
        persona = params.get("persona", "")
        if not persona:
            return "你想切换成什么角色？请告诉我。"

        if self.agent and hasattr(self.agent, "switch_persona"):
            await self.agent.switch_persona(persona)
            return f"✅ 已切换为「{persona}」模式。"
        return f"⚠️ 无法切换角色，人格系统未启用。"

    async def _toggle_proactive(self, params: dict) -> str:
        """开关活人感"""
        enabled_text = params.get("enabled", "")
        if "开启" in enabled_text:
            enabled = True
        elif "关闭" in enabled_text:
            enabled = False
        else:
            return "请指定「开启」或「关闭」。"

        if self.agent and hasattr(self.agent, "set_proactive"):
            await self.agent.set_proactive(enabled)
            status = "开启" if enabled else "关闭"
            return f"✅ 已{status}活人感模式。"
        return f"⚠️ 无法{status}活人感模式。"

    async def _get_status(self) -> str:
        """获取状态"""
        if self.agent and hasattr(self.agent, "get_status"):
            return await self.agent.get_status()

        # 默认状态
        status = {
            "status": "running",
            "tools": len(self.tools) if hasattr(self, "tools") else 0,
            "timestamp": datetime.now().isoformat(),
        }
        return json.dumps(status, ensure_ascii=False, indent=2)

    # ----- 剪贴板 -----
    def _clipboard_read(self) -> str:
        """读取剪贴板"""
        try:
            import pyperclip

            content = pyperclip.paste()
            if not content:
                return "剪贴板为空。"
            return f"📋 剪贴板内容:\n{content[:500]}"
        except ImportError:
            # 尝试使用系统命令
            return self._clipboard_read_fallback()

    def _clipboard_read_fallback(self) -> str:
        """剪贴板读取降级方案"""
        system = platform.system()
        try:
            if system == "Windows":
                result = subprocess.run(
                    ["powershell", "-command", "Get-Clipboard"], capture_output=True, text=True
                )
                content = result.stdout
            elif system == "Darwin":  # macOS
                result = subprocess.run(["pbpaste"], capture_output=True, text=True)
                content = result.stdout
            elif system == "Linux":
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True
                )
                content = result.stdout
            else:
                return "剪贴板功能需要安装 pyperclip: pip install pyperclip"

            if not content:
                return "剪贴板为空。"
            return f"📋 剪贴板内容:\n{content[:500]}"
        except Exception as e:
            return f"读取剪贴板失败: {e}"

    def _clipboard_write(self, params: dict) -> str:
        """写入剪贴板"""
        content = params.get("content", "")
        if not content:
            return "没有内容可复制。"

        try:
            import pyperclip

            pyperclip.copy(content)
            return f"✅ 已复制到剪贴板:\n{content[:200]}"
        except ImportError:
            return self._clipboard_write_fallback(content)

    def _clipboard_write_fallback(self, content: str) -> str:
        """剪贴板写入降级方案"""
        system = platform.system()
        try:
            if system == "Windows":
                subprocess.run(
                    ["powershell", "-command", f'Set-Clipboard -Value "{content}"'],
                    capture_output=True,
                    text=True,
                )
            elif system == "Darwin":  # macOS
                subprocess.run(["pbcopy"], input=content.encode(), text=True)
            elif system == "Linux":
                subprocess.run(
                    ["xclip", "-selection", "clipboard"], input=content.encode(), text=True
                )
            else:
                return "剪贴板功能需要安装 pyperclip: pip install pyperclip"
            return f"✅ 已复制到剪贴板:\n{content[:200]}"
        except Exception as e:
            return f"写入剪贴板失败: {e}"

    # ----- 实用工具 -----
    def _random_number(self, params: dict) -> str:
        """生成随机数"""
        min_val = int(params.get("min", 1))
        max_val = int(params.get("max", 100))

        if min_val > max_val:
            min_val, max_val = max_val, min_val

        result = random.randint(min_val, max_val)
        return f"🎲 随机数（{min_val} ~ {max_val}）: **{result}**"

    def _password_generate(self, params: dict) -> str:
        """生成随机密码"""
        length = int(params.get("length", 12))
        complexity = params.get("complexity", "中等")

        if length < 4:
            length = 4
        if length > 64:
            length = 64

        if complexity == "简单":
            chars = "abcdefghijklmnopqrstuvwxyz0123456789"
        elif complexity == "复杂":
            chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
        else:  # 中等
            chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

        password = "".join(random.choice(chars) for _ in range(length))
        return f"🔐 生成的{complexity}密码（{length}位）:\n`{password}`"

    async def _qr_generate(self, params: dict) -> str:
        """生成二维码"""
        content = params.get("content", "")
        if not content:
            return "请提供要生成二维码的内容。"

        try:
            import qrcode
            from io import BytesIO
            import base64

            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(content)
            qr.make(fit=True)

            img = qr.make_image(fill_color="black", back_color="white")
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode()

            return f"✅ 二维码已生成:\n![QR Code](data:image/png;base64,{img_base64})"
        except ImportError:
            return "二维码功能需要安装 qrcode: pip install qrcode[pil]"

    async def _shorten_url(self, params: dict) -> str:
        """缩短 URL"""
        url = params.get("url", "")
        if not url:
            return "请提供要缩短的 URL。"

        # 这里可以集成短链接 API
        return f"短链接功能需要配置 API 服务。原链接: {url}"

    # ----- 开发辅助 -----
    def _format_code(self, params: dict) -> str:
        """格式化代码"""
        code = params.get("code", "")
        language = params.get("language", "python")

        if not code:
            return "请提供要格式化的代码。"

        try:
            if language == "json":
                formatted = json.dumps(json.loads(code), ensure_ascii=False, indent=2)
                return f"```json\n{formatted}\n```"
            elif language == "python":
                import black

                formatted = black.format_str(code, mode=black.Mode())
                return f"```python\n{formatted}\n```"
            else:
                return f"暂不支持 {language} 格式化。\n```\n{code}\n```"
        except Exception as e:
            return f"格式化失败: {e}\n```\n{code}\n```"

    def _json_validate(self, params: dict) -> str:
        """验证 JSON"""
        json_str = params.get("json", "")
        if not json_str:
            return "请提供要验证的 JSON 字符串。"

        try:
            data = json.loads(json_str)
            return f"✅ JSON 格式正确\n```json\n{json.dumps(data, ensure_ascii=False, indent=2)[:500]}\n```"
        except json.JSONDecodeError as e:
            return f"❌ JSON 格式错误: {e}"

    def _timestamp_convert(self, params: dict) -> str:
        """时间戳转换"""
        ts = params.get("timestamp", "")
        direction = params.get("direction", "")

        if ts:
            try:
                ts_int = int(ts)
                # 处理毫秒级时间戳
                if ts_int > 10000000000:
                    ts_int = ts_int // 1000
                dt = datetime.fromtimestamp(ts_int)
                return f"📅 时间戳 {ts} 对应日期: {dt.strftime('%Y-%m-%d %H:%M:%S')}"
            except:
                pass

        # 如果没有时间戳，返回当前时间戳
        current_ts = int(datetime.now().timestamp())
        return f"🕐 当前时间戳: {current_ts}"

    def _base64_encode(self, params: dict) -> str:
        """Base64 编码"""
        text = params.get("text", "")
        if not text:
            return "请提供要编码的文本。"

        encoded = base64.b64encode(text.encode()).decode()
        return f"📦 Base64 编码结果:\n`{encoded}`"

    def _base64_decode(self, params: dict) -> str:
        """Base64 解码"""
        data = params.get("data", "")
        if not data:
            return "请提供要解码的 Base64 数据。"

        try:
            decoded = base64.b64decode(data).decode()
            return f"📄 Base64 解码结果:\n{decoded}"
        except Exception as e:
            return f"❌ 解码失败: {e}"

    # ----- 媒体操作 -----
    async def _take_photo(self) -> str:
        """拍照"""
        # 需要调用摄像头，暂时返回提示
        return "📷 拍照功能需要配置摄像头访问权限。"

    async def _play_audio(self, params: dict) -> str:
        """播放音频"""
        file = params.get("file", "")
        if not file:
            return "请提供要播放的音频文件路径。"
        return f"🔊 播放音频: {file}（需要配置音频播放器）"

    # ----- 信息查询 -----
    def _get_ip(self) -> str:
        """获取 IP 地址"""
        try:
            import socket

            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)

            # 尝试获取公网 IP
            try:
                import urllib.request

                with urllib.request.urlopen("https://api.ipify.org", timeout=5) as response:
                    public_ip = response.read().decode()
                return f"🌐 本机 IP: {local_ip}\n🌍 公网 IP: {public_ip}"
            except:
                return f"🌐 本机 IP: {local_ip}"
        except Exception as e:
            return f"获取 IP 失败: {e}"

    async def _get_location(self) -> str:
        """获取地理位置"""
        return "📍 地理位置获取需要 GPS 或 IP 定位服务。"

    def _get_battery(self) -> str:
        """获取电池信息"""
        try:
            import psutil

            battery = psutil.sensors_battery()
            if battery:
                percent = battery.percent
                plugged = "充电中" if battery.power_plugged else "使用电池"
                return f"🔋 电池电量: {percent}% ({plugged})"
            return "未检测到电池（可能是台式机）"
        except ImportError:
            return "电池信息需要安装 psutil: pip install psutil"
        except Exception:
            return "无法获取电池信息"
