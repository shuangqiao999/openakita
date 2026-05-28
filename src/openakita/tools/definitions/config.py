"""
系统配置工具定义

统一的 system_config 工具，用户通过聊天即可查看/修改所有系统配置。
支持：查看配置、修改设置、LLM 端点管理、UI 偏好、动态配置发现。
"""

CONFIG_TOOLS = [
    {
        "name": "system_config",
        "category": "Config",
        "description": (
            "Unified system configuration tool. When user wants to: "
            "(1) view or change any system setting (log level, thinking mode, proxy, IM channel, etc.), "
            "(2) add/remove/toggle/test LLM endpoints, or select the current conversation's LLM endpoint, "
            "(3) switch UI theme or language, "
            "(4) discover what settings are available, "
            "(5) manage LLM providers (add/update/remove custom providers), "
            "(6) check external extension modules (opencli, cli-anything) status and install/upgrade commands. "
            "IMPORTANT: If the user wants to switch/use/select an existing model endpoint for this chat, "
            "call action=select_endpoint. Do NOT use add_endpoint unless the user is creating a new endpoint. "
            "Before calling action=set, action=add_endpoint, or action=manage_provider with add/update/remove, "
            "ALWAYS use ask_user first to confirm the changes with the user. "
            "If unsure which config key to use, call action=discover first."
        ),
        "detail": """统一系统配置工具。

action 说明:
- discover: 列出所有可配置项，支持 category 过滤
- get: 读取当前配置值（敏感字段自动脱敏）
- set: 更新 .env 文件并热重载。key 用大写环境变量名，自动类型校验，只读字段被拒绝
- add_endpoint: 添加 LLM 端点，自动补全默认 base_url 和 api_type，API Key 存入 .env
- remove_endpoint: 按名称删除 LLM 端点
- toggle_endpoint: 按名称启用/停用端点
- select_endpoint: 临时切换当前会话的聊天模型端点（不改配置文件）。endpoint_name 为 auto/default/默认 时恢复默认
- test_endpoint: 测试端点连通性，返回延迟和状态
- set_ui: 切换桌面主题/语言
- manage_provider: 管理 LLM 服务商列表。list/add/update/remove，slug 只允许小写字母数字连字符，api_type 只允许 openai/anthropic
- extensions: status=查看外部模块安装状态，credits=致谢信息""",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "discover",
                        "get",
                        "set",
                        "add_endpoint",
                        "remove_endpoint",
                        "toggle_endpoint",
                        "select_endpoint",
                        "test_endpoint",
                        "set_ui",
                        "manage_provider",
                        "extensions",
                    ],
                    "description": "操作类型",
                },
                "category": {
                    "type": "string",
                    "description": (
                        "配置分类过滤（discover/get 时可选）。"
                        "常见分类: Agent, LLM, 日志, 代理, IM/Telegram, IM/飞书, IM/思维链推送, "
                        "会话, 定时任务, 人格, 活人感, 桌面通知, Embedding/记忆搜索, 语音识别 等。"
                        "调用 discover 不带 category 可查看所有分类。"
                    ),
                },
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "指定查询的配置字段名列表（get 时可选，如 ['log_level', 'thinking_mode']）",
                },
                "updates": {
                    "type": "object",
                    "description": (
                        "要修改的配置键值对（set 时必填）。"
                        'key 使用大写环境变量名，如 {"LOG_LEVEL": "DEBUG", "PROACTIVE_ENABLED": "true"}'
                    ),
                },
                "endpoint": {
                    "type": "object",
                    "description": (
                        "LLM 端点配置（add_endpoint 时必填）。"
                        "字段: name(必填), provider(必填), model(必填), "
                        "api_key(可选,存入.env), api_type(可选,自动推断), "
                        "base_url(可选,自动补全), priority(可选,默认10), "
                        "max_tokens(可选), context_window(可选), timeout(可选), "
                        "capabilities(可选,如['text','tools','vision'])"
                    ),
                    "properties": {
                        "name": {"type": "string", "description": "端点唯一名称"},
                        "provider": {
                            "type": "string",
                            "description": "服务商 slug（如 openai, anthropic, deepseek, dashscope, ollama 等）",
                        },
                        "model": {"type": "string", "description": "模型名称"},
                        "api_key": {
                            "type": "string",
                            "description": "API Key（会自动存入 .env，不存入 JSON）",
                        },
                        "api_type": {
                            "type": "string",
                            "enum": ["openai", "anthropic"],
                            "description": "API 协议类型（不填则根据 provider 自动推断）",
                        },
                        "base_url": {
                            "type": "string",
                            "description": "API 地址（不填则根据 provider 自动补全）",
                        },
                        "priority": {
                            "type": "integer",
                            "description": "优先级，数字越小越优先（默认 10）",
                        },
                        "max_tokens": {"type": "integer", "description": "最大输出 token 数"},
                        "context_window": {"type": "integer", "description": "上下文窗口大小"},
                        "timeout": {"type": "integer", "description": "请求超时（秒）"},
                        "capabilities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "模型能力列表，如 ['text','tools','vision','thinking']",
                        },
                    },
                    "required": ["name", "provider", "model"],
                },
                "endpoint_name": {
                    "type": "string",
                    "description": (
                        "端点名称（remove_endpoint / test_endpoint / select_endpoint 时必填）。"
                        "select_endpoint 可用 auto/default/默认 恢复默认模型。"
                    ),
                },
                "target": {
                    "type": "string",
                    "enum": ["main", "compiler", "stt"],
                    "description": "端点类型（默认 main）: main=主端点, compiler=Prompt编译, stt=语音识别",
                },
                "theme": {
                    "type": "string",
                    "enum": ["light", "dark", "system"],
                    "description": "UI 主题（set_ui 时）",
                },
                "language": {
                    "type": "string",
                    "enum": ["zh", "en"],
                    "description": "UI 语言（set_ui 时）",
                },
                "operation": {
                    "type": "string",
                    "enum": ["list", "add", "update", "remove", "status", "credits"],
                    "description": (
                        "操作子类型。manage_provider 时: list/add/update/remove；"
                        "extensions 时: status/credits"
                    ),
                },
                "provider": {
                    "type": "object",
                    "description": (
                        "服务商配置（manage_provider 的 add/update 时必填）。"
                        "add 必填: slug, name, api_type, default_base_url。"
                        "update 必填: slug（定位），其余为要修改的字段。"
                    ),
                    "properties": {
                        "slug": {
                            "type": "string",
                            "description": "服务商唯一标识（小写字母、数字、连字符）",
                        },
                        "name": {"type": "string", "description": "显示名称"},
                        "api_type": {
                            "type": "string",
                            "enum": ["openai", "anthropic"],
                            "description": "API 协议类型",
                        },
                        "default_base_url": {"type": "string", "description": "默认 API 地址"},
                        "api_key_env_suggestion": {
                            "type": "string",
                            "description": "建议的 API Key 环境变量名",
                        },
                        "supports_model_list": {
                            "type": "boolean",
                            "description": "是否支持拉取模型列表",
                        },
                        "requires_api_key": {"type": "boolean", "description": "是否需要 API Key"},
                        "is_local": {
                            "type": "boolean",
                            "description": "是否为本地服务（如 Ollama）",
                        },
                        "coding_plan_base_url": {
                            "type": "string",
                            "description": "Coding Plan 专用 API 地址",
                        },
                        "coding_plan_api_type": {
                            "type": "string",
                            "description": "Coding Plan 协议类型",
                        },
                    },
                },
                "slug": {
                    "type": "string",
                    "description": "服务商 slug（manage_provider 的 remove 时必填）",
                },
            },
            "required": ["action"],
        },
        "triggers": [
            "User wants to view or change system settings",
            "User asks about available configuration options",
            "User wants to add, remove, or test LLM endpoints",
            "User wants to switch, select, or use an existing model/LLM endpoint for the current chat",
            "User wants to switch theme or language",
            "User wants to add, modify, or remove LLM providers/服务商",
            "User asks about external modules/extensions status, install, or upgrade",
            "User asks about opencli or cli-anything",
        ],
        "examples": [
            {
                "scenario": "查看所有可配置项",
                "params": {"action": "discover"},
            },
            {
                "scenario": "查看 Agent 相关配置",
                "params": {"action": "get", "category": "Agent"},
            },
            {
                "scenario": "修改日志级别",
                "params": {"action": "set", "updates": {"LOG_LEVEL": "DEBUG"}},
            },
            {
                "scenario": "添加 DeepSeek 端点",
                "params": {
                    "action": "add_endpoint",
                    "endpoint": {
                        "name": "deepseek-chat",
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "api_key": "sk-xxx",
                    },
                },
            },
            {
                "scenario": "切换暗色主题",
                "params": {"action": "set_ui", "theme": "dark"},
            },
            {
                "scenario": "列出所有 LLM 服务商",
                "params": {"action": "manage_provider", "operation": "list"},
            },
            {
                "scenario": "添加自定义服务商",
                "params": {
                    "action": "manage_provider",
                    "operation": "add",
                    "provider": {
                        "slug": "my-proxy",
                        "name": "My API Proxy",
                        "api_type": "openai",
                        "default_base_url": "https://my-proxy.example.com/v1",
                        "api_key_env_suggestion": "MY_PROXY_API_KEY",
                    },
                },
            },
            {
                "scenario": "查看外部扩展模块状态",
                "params": {"action": "extensions", "operation": "status"},
            },
            {
                "scenario": "查看外部模块致谢",
                "params": {"action": "extensions", "operation": "credits"},
            },
        ],
    },
]
