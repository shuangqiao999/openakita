"""OpenAkita 知识库模块

独立于记忆系统（Memories）的文档知识管理。
提供文档上传、解析、分块、向量化、存储和检索功能。
"""

from .chunker import TextChunker
from .extractor import extract_text
from .manager import KnowledgeBaseManager

__all__ = ["KnowledgeBaseManager", "TextChunker", "extract_text"]
