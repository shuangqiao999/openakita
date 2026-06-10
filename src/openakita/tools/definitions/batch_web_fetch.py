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
            "从权威信息源直接抓取最新内容，当用户请求匹配以下主题时优先于 web_search 使用："
            "最新技术新闻/AI研究进展/学术论文/开源项目动态/技术博客/科技商业新闻/"
            "公开数据集/国际新闻/编程技术问答/官方文档/地缘分析/技术社会分析。"
            "比通用搜索引擎更快且来源更可靠。"
        ),
        "detail": (
            "## 使用场景\n"
            "| 用户意图 | 调用方式 |\n"
            "|----------|----------|\n"
            '| 最新技术新闻、GitHub趋势、Hacker News | purpose="daily_tech_news" |\n'
            '| AI最新进展、OpenAI/Google/HuggingFace | purpose="ai_research" |\n'
            '| 学术论文、arXiv、JMLR | purpose="academic_papers" |\n'
            '| 开源项目动态、Gitee/开源中国 | purpose="development" |\n'
            '| 技术博客、美团/Microsoft | purpose="technical_blog" |\n'
            '| 科技商业新闻、36Kr/VentureBeat | purpose="tech_news" |\n'
            '| 公开数据集、World Bank/Common Crawl | purpose="public_data" |\n'
            '| 国际新闻、Reuters/Guardian | purpose="general_news" |\n'
            '| Stack Overflow技术问答 | purpose="technical_qna" |\n'
            '| Python官方文档 | purpose="official_doc" |\n'
            '| 地缘政治分析 | purpose="geo_analysis" |\n'
            '| 技术与社会分析 | purpose="tech_analysis" |\n\n'
            "## 何时不用\n"
            "- 用户没有指定具体固定来源 → 用 web_search\n"
            "- 用户给出了具体 URL → 用 batch_web_fetch 或 web_fetch\n\n"
            "## 注意\n"
            "- purpose 必须从可用列表精确选择\n"
            "- 工具按 priority 排序抓取（priority 越高越优先）\n"
            "- 单个书签抓取失败不影响其他书签"
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
                    "description": "书签用途类别，必须从可用列表中选择",
                    "enum": [
                        "daily_tech_news",
                        "ai_research",
                        "academic_papers",
                        "development",
                        "technical_blog",
                        "tech_news",
                        "tech_analysis",
                        "geo_analysis",
                        "public_data",
                        "general_news",
                        "technical_qna",
                        "official_doc",
                    ],
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
