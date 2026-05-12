"""
OpenAkita MCP 服务器模块

包含内置的 MCP 服务器实现：
- web_search: 六引擎并行搜索 (Bing/百度/360/搜狗/神马/头条)，DDG 兜底
"""

from .web_search import mcp as web_search_mcp

__all__ = ["web_search_mcp"]

