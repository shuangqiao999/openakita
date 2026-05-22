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
- doc_filter: 可选，按文档 ID 过滤搜索范围

**注意**：知识库为空时不会返回结果。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20, "description": "返回结果数"},
                "doc_filter": {"type": "string", "description": "可选，按文档 ID 过滤搜索范围"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "save_to_knowledge_base",
        "category": "Knowledge Base",
        "description": "将文本内容保存到知识库。适用场景：用户要求收藏网页内容、保存参考文档片段、记录重要文本材料。",
        "detail": """将任意文本内容作为文档存入知识库。系统会自动分块、向量化，使其可被后续搜索。

**适用场景**：
- 用户说"把这个网页保存到知识库"
- 用户要求"记录下这段内容"
- 需要持久化保存某个参考材料

**参数**：
- title: 文档标题（必填），将作为知识库中显示的文件名
- content: 要保存的完整文本内容（必填）

**注意**：内容过短（<50字符）可能无法有效分块和检索。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "文档标题"},
                "content": {"type": "string", "description": "要保存的文本内容"},
            },
            "required": ["title", "content"],
        },
    },
]
