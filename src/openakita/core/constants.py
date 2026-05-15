"""
Core shared constants extracted from individual modules.

Purpose:
- Eliminate duplicate constant definitions across modules
- Reduce module-level clutter (marker lists, regexes, threshold values)
- Provide a single source of truth for cross-cutting constants
"""

import re
from pathlib import Path

# ── Context / token constants (from context_manager.py) ──
CHARS_PER_TOKEN: int = 2
CHUNK_MAX_TOKENS: int = 30000
CONTEXT_BOUNDARY_MARKER: str = "[上下文边界]"

# ── Tool output constants (from tool_executor.py) ──
DEFAULT_TOOL_RESULT_MAX_CHARS: int = 32000
MAX_TOOL_RESULT_CHARS: int = DEFAULT_TOOL_RESULT_MAX_CHARS
OVERFLOW_MARKER: str = "[OUTPUT_TRUNCATED]"

# ── SSE / streaming constants (from reasoning_engine.py) ──
_SSE_RESULT_PREVIEW_CHARS: int = 32000

# ── Recovery / error markers (from reasoning_engine.py) ──
_RECOVERABLE_TOOL_ERROR_MARKERS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "connection",
    "rate limit",
    "too many requests",
    "service unavailable",
    "try again",
)

# ── Supervisor thresholds (from supervisor.py) ──
TOKEN_ANOMALY_THRESHOLD: int = 40000
SIGNATURE_REPEAT_WARN: int = 3
SIGNATURE_REPEAT_TERMINATE: int = 5
SELF_CHECK_INTERVAL: int = 10
EXTREME_ITERATION_THRESHOLD: int = 50

# ── Tool names: admin (no artifact) tools (from reasoning_engine.py) ──
_ADMIN_TOOL_NAMES: frozenset[str] = frozenset({
    "get_todo_status",
    "search_memory",
    "list_directory",
    "switch_mode",
    "read_file",
    "web_search",
    "web_fetch",
})

# ── Tool names: readonly exploration (from reasoning_engine.py) ──
_READONLY_EXPLORATION_TOOLS: frozenset[str] = frozenset({
    "web_fetch",
    "web_search",
    "news_search",
    "list_directory",
    "read_file",
    "search_memory",
    "get_todo_status",
    "check_python_env",
    "list_agents",
    "list_providers",
    "get_system_status",
})

# ── Tool names: cacheable readonly tools (from reasoning_engine.py) ──
_CACHEABLE_READONLY_TOOLS: frozenset[str] = frozenset({"web_fetch", "web_search", "news_search"})

# ── External tool markers (from agent.py) ──
_EXTERNAL_TOOL_MARKERS: tuple[str, ...] = (
    "打开网页", "搜索网页", "浏览网页", "访问网页",
    "搜索最新", "搜索新闻", "查询最新",
    "发送邮件", "发邮件", "写邮件",
    "安装软件", "安装包", "安装依赖",
    "部署应用", "部署服务", "部署到",
    "创建仓库", "fork", "clone",
    "生成图片", "画图", "作图", "生成图像",
    "处理视频", "剪辑视频", "视频处理",
    "访问数据库", "查询数据库", "连接数据库",
    "Docker", "docker", "容器",
    "Kubernetes", "k8s", "集群",
    "调用API", "请求API", "接口调用",
    "下载文件", "下载数据",
    "上传文件", "上传到",
    "扫描端口", "端口扫描",
    "抓取数据", "爬虫", "数据采集",
    "语音合成", "文字转语音", "TTS",
    "语音识别", "ASR", "语音转文字",
    "翻译文档", "文档翻译",
    "编译代码", "构建项目",
    "运行测试", "执行测试",
    "格式化代码", "代码格式化",
    "检查代码", "代码检查", "lint",
)

# ── Task response quality markers (from agent.py) ──
_TASK_RESULT_META_MARKERS: tuple[str, ...] = (
    "已完成", "已执行", "已处理", "已更新",
    "已完成任务", "任务完成", "执行完毕",
    "操作完成", "处理完成", "更新完成",
    "已经完成", "已经执行", "已经处理",
    "已经更新", "已成功", "已经成功",
    "successfully", "completed",
    "done", "finished",
    "全部完成", "所有任务",
)

_TASK_PROGRESS_ONLY_MARKERS: tuple[str, ...] = (
    "正在", "开始", "准备",
    "第一步", "第二步", "第三步",
    "接下来", "然后",
    "首先", "其次",
)

# ── Replay request markers (from agent.py) ──
_REPLAY_REQUEST_MARKERS: tuple[str, ...] = (
    "重新回答", "再回答一次", "再说一遍",
    "再回答", "重新说", "复述",
    "重复一遍", "再说一次", "再讲一遍",
    "重新输出", "再输出一次",
    "再生成", "再生成一次",
    "再执行", "重新执行",
    "重新运行", "再运行一次",
    "再来一遍", "重来", "redo",
)

# ── Risk / destructive verbs (from agent.py) ──
_DESTRUCTIVE_VERBS: tuple[str, ...] = (
    "删除", "移除", "清空", "格式化",
    "卸载", "解绑", "注销", "销毁",
    "覆盖", "替换", "重置",
)

# ── Tool limit constants (from reasoning_engine.py) ──
_READONLY_STAGNATION_LIMIT: int = 3

# ── Overflow constants (from tool_executor.py) ──
_OVERFLOW_DIR: Path = Path("data/tool_overflow")
_OVERFLOW_MAX_FILES: int = 200

# ── Image / media constants (from agent.py) ──
_INLINE_IMAGE_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB

# ── Compression / tool budget constants (from agent.py) ──
COMPRESSION_RATIO: float = 0.15
LARGE_TOOL_RESULT_THRESHOLD: int = 5000
MIN_RECENT_TURNS: int = 4

# ── Tool classification sets (from agent.py) ──
SMALL_CTX_CORE_TOOLS: frozenset[str] = frozenset({
    "create_todo", "read_file", "write_file",
    "list_directory", "search_memory", "get_todo_status",
    "switch_mode", "web_fetch", "web_search",
    "run_shell_command", "install_package",
})

_MEDIUM_CTX_EXTRA_TOOLS: frozenset[str] = frozenset({
    "run_shell_command", "install_package", "uninstall_package",
    "create_workspace", "list_agents", "create_agent",
    "publish_agent", "search_agent_store",
    "web_fetch", "web_search", "news_search",
    "create_mcp_server", "list_mcp_servers",
    "install_skill", "list_marketplace_skills",
})

# ── Regex patterns ──
_LOCAL_UPLOAD_RE: re.Pattern = re.compile(r"^https?://[^/]+/api/local-upload/([a-f0-9-]+)")
_DATA_URI_RE: re.Pattern = re.compile(
    r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)", re.IGNORECASE
)
_LEADING_TIMESTAMP_RE: re.Pattern = re.compile(r"^[\[\(]\d{1,2}:\d{2}(:\d{2})?[\]\)]\s*")
_INTENT_TAG_RE: re.Pattern = re.compile(r"\[(ACTION|REPLY|ASK|ANALYZE|THINK)\]")

# ── Unix-style file permission mode directives ──
WRITE_MODE_READONLY: int = 0o444
WRITE_MODE_EXECUTABLE: int = 0o755
