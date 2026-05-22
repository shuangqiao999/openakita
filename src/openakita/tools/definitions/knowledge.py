"""知识库工具定义"""

KNOWLEDGE_BASE_TOOLS = [
    {
        "name": "search_knowledge_base",
        "category": "Knowledge Base",
        "description": "在用户上传的个人文档库中搜索相关内容，返回最相关的片段。当需要参考用户提供的文档、报告、手册时使用。",
        "detail": """搜索用户上传到知识库的文档内容。

**适用场景**：
- 用户询问某份上传文档中的具体内容
- 需要引用某份技术文档或报告的信息
- 查找用户项目文档中的具体细节

**参数**：
- query: 搜索查询词，越具体越好
- top_k: 返回结果数，默认 5

**注意**：知识库为空时不会返回结果。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10, "description": "返回结果数"},
            },
            "required": ["query"],
        },
    },
]
