"""
Web Search MCP 服务器

搜索引擎优先级：Bing（中国可访问）> DuckDuckGo（兜底）

启动方式：
    python -m openakita.mcp_servers.web_search

工具：
    - web_search: 搜索网页
    - news_search: 搜索新闻
"""

import logging
import re
import traceback

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ── Bing HTML 搜索（中国可直接访问）────────────────────────

_BING_SEARCH_URL = "https://cn.bing.com/search"
_BING_NEWS_URL = "https://cn.bing.com/news/search"
_BING_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_BING_ALGO_RE = re.compile(r'<li\s+class="b_algo"[^>]*>(.*?)</li>', re.DOTALL)
_BING_TITLE_RE = re.compile(
    r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>\s*</h2>', re.DOTALL
)
_BING_SNIPPET_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL)
_BING_CAPTION_RE = re.compile(r'class="b_caption"[^>]*>(.*?)</div>', re.DOTALL)
_BING_STRIP_RE = re.compile(r"<[^>]+>")
_BING_ENTITY_RE = re.compile(r"&[a-z]+;")
_BING_WS_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    text = _BING_STRIP_RE.sub(" ", text)
    text = _BING_ENTITY_RE.sub(" ", text)
    return _BING_WS_RE.sub(" ", text).strip()


def _fetch_bing_html(url: str, params: dict) -> str | None:
    import httpx

    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(
                url,
                params=params,
                headers={
                    "User-Agent": _BING_UA,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
            resp.raise_for_status()
            return resp.text
    except Exception as exc:
        logger.warning(f"Bing search HTTP failed: {type(exc).__name__}: {exc}")
        return None


def _parse_bing_web_results(html: str, max_results: int) -> list[dict]:
    results: list[dict] = []
    blocks = _BING_ALGO_RE.findall(html)
    if not blocks:
        alt_re = re.compile(r'<li[^>]*class="b_algo[^"]*"[^>]*>(.+?)</li>', re.DOTALL)
        blocks = alt_re.findall(html)

    for block in blocks[:max_results]:
        title_match = _BING_TITLE_RE.search(block)
        if not title_match:
            continue
        url = title_match.group(1)
        title = _strip_html(title_match.group(2))

        snippet = ""
        snippet_match = _BING_SNIPPET_RE.search(block)
        if snippet_match:
            snippet = _strip_html(snippet_match.group(1))
        if not snippet:
            cap_match = _BING_CAPTION_RE.search(block)
            if cap_match:
                snippet = _strip_html(cap_match.group(1))

        results.append({"title": title, "href": url, "body": snippet})

    return results


def _bing_web_search(query: str, max_results: int) -> list[dict]:
    html = _fetch_bing_html(_BING_SEARCH_URL, {"q": query, "count": max_results})
    if not html:
        return []
    return _parse_bing_web_results(html, max_results)


def _bing_news_search(query: str, max_results: int) -> list[dict]:
    html = _fetch_bing_html(_BING_NEWS_URL, {"q": query, "count": max_results})
    if not html:
        return []
    results = _parse_bing_web_results(html, max_results)
    for r in results:
        r.setdefault("date", "")
        r.setdefault("source", "")
    return results


def _ddg_web_search(query: str, max_results: int, region: str, safesearch: str) -> list[dict]:
    from ddgs import DDGS

    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results, region=region, safesearch=safesearch))


def _ddg_news_search(
    query: str, max_results: int, region: str, safesearch: str, timelimit: str | None
) -> list[dict]:
    from ddgs import DDGS

    with DDGS() as ddgs:
        return ddgs.news(
            query, max_results=max_results, region=region, safesearch=safesearch,
            timelimit=timelimit,
        )


# 创建 MCP 服务器实例
mcp = FastMCP(
    name="web-search",
    instructions="""Web Search MCP Server - Bing（优先）+ DuckDuckGo（兜底）双引擎搜索。

可用工具：
- web_search: 搜索网页，返回标题、链接和摘要
- news_search: 搜索新闻，返回最新新闻文章

搜索引擎自动选择：Bing 优先（中国可直接访问），DuckDuckGo 作为兜底。
""",
)


def _format_web_results(results: list) -> str:
    """格式化网页搜索结果"""
    if not results:
        return "未找到相关结果"

    output = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "无标题")
        url = r.get("href", r.get("link", ""))
        body = r.get("body", r.get("snippet", ""))
        output.append(f"**{i}. {title}**\n{url}\n{body}\n")

    return "\n".join(output)


def _format_news_results(results: list) -> str:
    """格式化新闻搜索结果"""
    if not results:
        return "未找到相关新闻"

    output = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "无标题")
        url = r.get("url", r.get("link", ""))
        body = r.get("body", r.get("excerpt", ""))
        date = r.get("date", "")
        source = r.get("source", "")

        header = f"**{i}. {title}**"
        if source or date:
            header += f" ({source} {date})"

        output.append(f"{header}\n{url}\n{body}\n")

    return "\n".join(output)


@mcp.tool()
def web_search(
    query: str, max_results: int = 5, region: str = "wt-wt", safesearch: str = "moderate"
) -> str:
    """
    Search the web — Bing first, DuckDuckGo as fallback.

    Args:
        query: Search query string
        max_results: Maximum number of results (default: 5, max: 20)
        region: Region code (default: "wt-wt" for worldwide, "cn-zh" for China)
        safesearch: Safe search level ("on", "moderate", "off")

    Returns:
        Formatted search results with title, URL, and snippet
    """
    max_results = min(max(1, max_results), 20)

    # ① Bing（中国可直接访问）
    try:
        results = _bing_web_search(query, max_results)
        if results:
            logger.info("Bing web search returned %d results for: %s", len(results), query)
            return _format_web_results(results)
    except Exception as e:
        logger.warning(f"Bing web search failed, falling back to DDG: {type(e).__name__}: {e}")

    # ② DuckDuckGo 兜底
    try:
        from ddgs import DDGS  # noqa: F401
    except ImportError:
        from openakita.tools._import_helper import import_or_hint
        return f"错误：{import_or_hint('ddgs')}"

    try:
        results = _ddg_web_search(query, max_results, region, safesearch)
        return _format_web_results(results)
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"All web search engines failed: {type(e).__name__}: {e}\n{tb}")
        return f"搜索失败 (Bing & DDG): {type(e).__name__}: {e}"


@mcp.tool()
def news_search(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timelimit: str | None = None,
) -> str:
    """
    Search news — Bing first, DuckDuckGo as fallback.

    Args:
        query: Search query string
        max_results: Maximum number of results (default: 5, max: 20)
        region: Region code (default: "wt-wt" for worldwide)
        safesearch: Safe search level ("on", "moderate", "off")
        timelimit: Time limit ("d" for day, "w" for week, "m" for month)

    Returns:
        Formatted news results with title, source, date, URL, and excerpt
    """
    max_results = min(max(1, max_results), 20)

    # ① Bing（中国可直接访问）
    try:
        results = _bing_news_search(query, max_results)
        if results:
            logger.info("Bing news search returned %d results for: %s", len(results), query)
            return _format_news_results(results)
    except Exception as e:
        logger.warning(f"Bing news search failed, falling back to DDG: {type(e).__name__}: {e}")

    # ② DuckDuckGo 兜底
    try:
        from ddgs import DDGS  # noqa: F401
    except ImportError:
        from openakita.tools._import_helper import import_or_hint
        return f"错误：{import_or_hint('ddgs')}"

    try:
        results = _ddg_news_search(query, max_results, region, safesearch, timelimit)
        return _format_news_results(results)
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"All news search engines failed: {type(e).__name__}: {e}\n{tb}")
        return f"新闻搜索失败 (Bing & DDG): {type(e).__name__}: {e}"


# 作为模块运行时启动服务器
if __name__ == "__main__":
    mcp.run()
