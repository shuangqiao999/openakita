"""batch_web_fetch / fetch_bookmarked 工具定义"""

BATCH_WEB_FETCH_TOOLS = [
    {
        "name": "batch_web_fetch",
        "category": "Web",
        "description": (
            "批量并发抓取多个网址的内容并转为 Markdown。适合一次性分析多个网页。"
            "自动处理重定向、超时重试、域名熔断。结果按输入顺序排列。"
        ),
        "related_tools": [
            {"name": "web_search", "relation": "没有具体 URL 时用 web_search 搜索"},
            {"name": "web_fetch", "relation": "单页面抓取时用 web_fetch"},
            {"name": "fetch_bookmarked", "relation": "按用途分类的预设网址抓取"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要抓取的网址列表",
                },
                "max_concurrent": {
                    "type": "integer",
                    "description": "最大并发数，默认 5",
                    "default": 5,
                },
                "timeout": {
                    "type": "integer",
                    "description": "每个请求超时秒数，默认 30",
                    "default": 30,
                },
            },
            "required": ["urls"],
        },
    },
    {
        "name": "fetch_bookmarked",
        "category": "Web",
        "description": (
            "从预设的书签配置中按用途分类抓取网址。适合获取固定来源的最新内容"
            "（如科技新闻、官方文档、学术论文）。书签配置在 web_bookmarks.json 中预设。"
        ),
        "related_tools": [
            {"name": "batch_web_fetch", "relation": "底层使用 batch_web_fetch 并发抓取"},
            {"name": "web_search", "relation": "开放域搜索替代方案"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "purpose": {
                    "type": "string",
                    "description": "书签用途类别（如 daily_tech_news、official_doc、academic_papers）",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多抓取该书签类别下的前 N 个网址，默认 5",
                    "default": 5,
                },
            },
            "required": ["purpose"],
        },
    },
]
