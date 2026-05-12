"""
OpenAkita MCP 服务器模块

包含内置的 MCP 服务器实现：
- web_search: Bing + DuckDuckGo 双引擎搜索（Bing 优先，DDG 兜底）
"""

from .web_search import mcp as web_search_mcp

__all__ = ["web_search_mcp"]

