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

当用户要求将网页内容保存到知识库时，应先用 web_fetch 获取网页正文，再调用本工具保存。

**适用场景**：
- 用户说"把这个网页保存到知识库"
- 用户要求"记录下这段内容"
- 需要持久化保存某个参考材料
- 将 web_fetch 抓取的网页内容存入知识库供长期检索

**参数**：
- title: 文档标题（必填），将作为知识库中显示的文件名
- content: 要保存的完整文本内容（必填）

**注意**：
- 内容过短（<50字符）可能无法有效分块和检索
- 如内容来自 web_fetch，应去除 [OPENAKITA_SOURCE] 元数据头和 URL 信息行，只保留正文""",
        "related_tools": [
            {"name": "web_fetch", "relation": "先抓取网页内容，再存入知识库"},
            {"name": "read_knowledge_base_document", "relation": "读取已保存的文档内容"},
            {"name": "search_knowledge_base", "relation": "搜索知识库中的内容"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "文档标题"},
                "content": {"type": "string", "description": "要保存的文本内容"},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "get_knowledge_base_status",
        "category": "Knowledge Base",
        "description": "查看知识库整体状态：文档总数、就绪/处理中/失败数量、分块总数、最近上传的文档。",
        "detail": """获取知识库的统计概览，包括文档数量、状态分布、最近上传列表。

**适用场景**：
- 用户问"知识库里有什么"、"知识库状态怎么样"
- 需要了解总共有多少文档和分块

**无需参数**。""",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_knowledge_base_documents",
        "category": "Knowledge Base",
        "description": "列出知识库中的文档列表，支持搜索和分页。",
        "detail": """列出知识库中已保存的文档。

**适用场景**：
- 用户问"知识库里有哪些文档"
- "列出知识库的文档"

**参数**：
- search: 可选，按名称模糊搜索
- limit: 返回数量，默认 10""",
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "可选，按名称模糊搜索"},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 30},
            },
        },
    },
    {
        "name": "delete_knowledge_base_document",
        "category": "Knowledge Base",
        "description": "从知识库中删除指定文档。支持按名称模糊匹配，删除匹配的第一个文档。",
        "detail": """从知识库中删除文档及其所有分块。

**适用场景**：
- 用户说"把红楼梦从知识库删掉"
- "删除知识库里的某个文档"

**参数**：
- name: 文档名称（支持模糊匹配）

**注意**：删除操作不可撤销。如匹配到多个文档，仅删除第一个。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要删除的文档名称"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "repair_knowledge_base_document",
        "category": "Knowledge Base",
        "description": "修复知识库中指定文档的向量索引（从分块重建 LanceDB 向量）。适用于文档搜索异常时恢复。",
        "detail": """从 SQLite 分块重建指定文档的向量索引。

**适用场景**：
- 用户说"修复红楼梦的索引"
- 某个文档搜索不到时应先尝试修复
- 系统提示文档数据不一致时

**参数**：
- name: 文档名称（支持模糊匹配），修复匹配的第一个文档""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要修复的文档名称"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "overwrite_knowledge_base_document",
        "category": "Knowledge Base",
        "description": "用新文本内容覆盖知识库中已存在的文档。先删除旧文档，再将新内容保存为同名文档。",
        "detail": """用新文本覆盖已有文档。

**适用场景**：
- 用户说"用这段新内容更新红楼梦"
- 用户修改了文档内容需要重新入库

**参数**：
- name: 要被覆盖的文档名称（支持模糊匹配，覆盖匹配的第一个）
- content: 新的文本内容

**注意**：会先删除旧文档及其所有分块，再保存新内容。旧文档的向量索引会被完全替换。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要覆盖的文档名称"},
                "content": {"type": "string", "description": "新的文本内容"},
            },
            "required": ["name", "content"],
        },
    },
    {
        "name": "read_knowledge_base_document",
        "category": "Knowledge Base",
        "description": "读取知识库中指定文档的完整文本内容。适用场景：用户要求查看某份文档的全文、需要引用文档中的具体段落。",
        "detail": """读取知识库文档的全文内容。

**适用场景**：
- 用户说"把知识库里XXX文档的全文显示出来"
- 需要了解某份文档的完整内容
- 在修改文档前先查看当前内容

**参数**：
- name: 文档名称（支持模糊匹配，读取匹配的第一个文档）
- max_chars: 最大返回字符数，默认 20000

**注意**：内容来自分块拼接，可能与原始文件格式略有差异。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要读取的文档名称"},
                "max_chars": {"type": "integer", "default": 20000, "minimum": 100, "maximum": 100000, "description": "最大返回字符数"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "update_knowledge_base_document",
        "category": "Knowledge Base",
        "description": "更新知识库中已有文档的文本内容，重新分块并入库。保留原文档 ID，不创建新文档。适用场景：用户要求修改某份文档、用新内容替换旧内容。",
        "detail": """更新知识库文档的文本内容，原地重新分块→嵌入→入库。

**适用场景**：
- 用户说"把XXX文档的内容改成..."
- 用户要求"更新知识库里的某份文档"
- 修改了文档内容需要重新入库

**参数**：
- name: 文档名称（支持模糊匹配，更新匹配的第一个）
- content: 新的完整文本内容

**注意**：
- 旧分块和向量数据会被删除后重建
- 文档 ID 保持不变
- 如未匹配到文档会返回错误""",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要更新的文档名称"},
                "content": {"type": "string", "description": "新的完整文本内容"},
            },
            "required": ["name", "content"],
        },
    },
]
