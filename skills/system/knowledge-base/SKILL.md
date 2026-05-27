---
name: knowledge-base
description: Manage the built-in knowledge base: search, save documents, read/edit/delete documents, view status. Use when the user asks about knowledge base operations, wants to save web content, or needs to retrieve previously stored documents.
system: true
handler: knowledge
tool-name: search_knowledge_base
category: Knowledge Base
---

# Knowledge Base Management

知识的增删改查全套工具。自动分块 + 向量化存储，支持语义搜索，跨文档关联。

## Tools

| 工具 | 用途 |
|------|------|
| `search_knowledge_base` | 语义搜索知识库内容 |
| `save_to_knowledge_base` | 保存文本到知识库 |
| `read_knowledge_base_document` | 读取文档全文 |
| `update_knowledge_base_document` | 更新文档内容 |
| `overwrite_knowledge_base_document` | 覆盖已有文档 |
| `list_knowledge_base_documents` | 列出文档列表 |
| `delete_knowledge_base_document` | 删除文档 |
| `repair_knowledge_base_document` | 修复文档向量索引 |
| `get_knowledge_base_status` | 查看知识库状态 |

## Workflow: Save Web Content to Knowledge Base

当用户要求将**网页内容**保存到知识库时：

1. 使用 `web_fetch` 获取网页正文（若只有 URL 而没有具体内容）
2. 提取网页正文，**去除** `[OPENAKITA_SOURCE]` 开头的元数据行、URL/Status 信息行
3. 以网页标题或用户指定名称作为 `title`
4. 调用 `save_to_knowledge_base(title=..., content=...)` 保存
5. 告知用户保存结果（文档名 + 分块数）

## Workflow: Update Existing Document

当用户要求**修改**知识库中文档时：

1. 先用 `read_knowledge_base_document` 获取当前全文
2. 根据用户要求修改内容
3. 调用 `update_knowledge_base_document(name=..., content=...)` 更新
4. 告知用户更新结果

## Examples

**保存网页到知识库**:
```
用户: 把 https://example.com/doc 保存到知识库
→ web_fetch(url="https://example.com/doc")
→ save_to_knowledge_base(title="example文档", content="...")
```

**搜索并引用**:
```
用户: 知识库里关于 asyncio 的内容有哪些？
→ search_knowledge_base(query="asyncio")
→ 基于搜索结果回答
```
