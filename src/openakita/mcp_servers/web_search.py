"""
Web Search MCP 服务器

六引擎并行搜索：Bing / Baidu / 360 / Sogou / Shenma / Toutiao
所有引擎同时发起搜索 → 合并结果 → 按URL去重 → 取前8条
DuckDuckGo 作为最后兜底

启动方式：
    python -m openakita.mcp_servers.web_search

工具：
    - web_search: 搜索网页
    - news_search: 搜索新闻
"""

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ── 公共 HTML 工具 ────────────────────────────────────────

_SEARCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_RE_STRIP = re.compile(r"<[^>]+>")
_RE_ENTITY = re.compile(r"&[a-z]+;")
_RE_WS = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    text = _RE_STRIP.sub(" ", text)
    text = _RE_ENTITY.sub(" ", text)
    return _RE_WS.sub(" ", text).strip()


def _fetch_html(url: str, params: dict, *, headers: dict | None = None) -> str | None:
    import httpx

    default_headers = {
        "User-Agent": _SEARCH_UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if headers:
        default_headers.update(headers)

    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(url, params=params, headers=default_headers)
            resp.raise_for_status()
            return resp.text
    except Exception as exc:
        logger.debug(f"HTTP fetch failed for {url}: {type(exc).__name__}: {exc}")
        return None


def _extract_url_from_baidu_redirect(raw_url: str) -> str:
    if not raw_url or "baidu.com" not in raw_url:
        return raw_url
    try:
        import html
        decoded = html.unescape(raw_url)
        m = re.search(r"(?:url|link)=([^&]+)", decoded)
        if m:
            from urllib.parse import unquote
            return unquote(m.group(1))
    except Exception:
        pass
    return raw_url


# ── 通用解析器工厂 ────────────────────────────────────────

def _make_standard_parser(
    block_re: re.Pattern,
    title_re: re.Pattern,
    snippet_re: re.Pattern,
    *,
    url_group: int = 1,
    title_group: int = 2,
    snippet_group: int = 1,
    url_formatter: Callable[[str], str] | None = None,
) -> Callable[[str, int], list[dict[str, Any]]]:
    def parse(html: str, max_results: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        blocks = block_re.findall(html)
        for block in blocks[:max_results]:
            tm = title_re.search(block)
            if not tm:
                continue
            url = tm.group(url_group)
            if url_formatter:
                url = url_formatter(url)
            title = _strip_html(tm.group(title_group))
            snippet = ""
            sm = snippet_re.search(block)
            if sm:
                snippet = _strip_html(sm.group(snippet_group))
            if title:
                results.append({"title": title, "href": url, "body": snippet})
        return results
    return parse


# ── 各搜索引擎实现 ────────────────────────────────────────

@dataclass
class SearchEngine:
    name: str
    label: str
    search_url: str
    search_params_fn: Callable[[str, int], dict]
    parse_fn: Callable[[str, int], list[dict[str, Any]]]
    extra_headers: dict | None = None


# Bing
_BING_BLOCK_RE = re.compile(r'<li\s+class="b_algo"[^>]*>(.*?)</li>', re.DOTALL)
_BING_TITLE_RE = re.compile(
    r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>\s*</h2>', re.DOTALL
)
_BING_SNIPPET_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL)

ENGINE_BING = SearchEngine(
    name="bing", label="Bing",
    search_url="https://cn.bing.com/search",
    search_params_fn=lambda q, n: {"q": q, "count": n},
    parse_fn=_make_standard_parser(_BING_BLOCK_RE, _BING_TITLE_RE, _BING_SNIPPET_RE),
)

# 百度
_BAIDU_BLOCK_RE = re.compile(
    r'<div[^>]*class="(?:result|c-container)[^"]*"[^>]*cachable[^>]*>(.+?)</div>\s*(?:</div>)?',
    re.DOTALL,
)
_BAIDU_BLOCK_ALT_RE = re.compile(
    r'<div[^>]*class="(?:result|c-container)[^"]*"[^>]*>(.+?)(?:</div>\s*</div>|</div>\s*$|<div[^>]*class="(?:result|c-container))',
    re.DOTALL,
)
_BAIDU_TITLE_RE = re.compile(
    r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>\s*</h3>', re.DOTALL
)
_BAIDU_SNIPPET_RE = re.compile(
    r'<(?:span[^>]*class="[^"]*content-right[^"]*"|div[^>]*class="c-abstract"[^>]*|span[^>]*class="c-color"[^>]*)>(.+?)</(?:span|div)>',
    re.DOTALL,
)
_BAIDU_SNIPPET_ALT_RE = re.compile(
    r'<(?:span|div|p)[^>]*class="[^"]*abstract[^"]*"[^>]*>(.*?)</(?:span|div|p)>', re.DOTALL
)


def _parse_baidu(html: str, max_results: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    blocks = _BAIDU_BLOCK_RE.findall(html) or _BAIDU_BLOCK_ALT_RE.findall(html)
    for block in blocks[:max_results]:
        tm = _BAIDU_TITLE_RE.search(block)
        if not tm:
            continue
        url = _extract_url_from_baidu_redirect(tm.group(1))
        title = _strip_html(tm.group(2))
        snippet = ""
        sm = _BAIDU_SNIPPET_RE.search(block) or _BAIDU_SNIPPET_ALT_RE.search(block)
        if sm:
            snippet = _strip_html(sm.group(1))
        if title:
            results.append({"title": title, "href": url, "body": snippet})
    return results


ENGINE_BAIDU = SearchEngine(
    name="baidu", label="百度",
    search_url="https://www.baidu.com/s",
    search_params_fn=lambda q, n: {"wd": q, "rn": str(n)},
    parse_fn=_parse_baidu,
    extra_headers={"Referer": "https://www.baidu.com/"},
)

# 360
_SO360_BLOCK_RE = re.compile(r'<li\s+class="res-list"[^>]*>(.*?)</li>', re.DOTALL)
_SO360_TITLE_RE = re.compile(
    r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>\s*</h3>', re.DOTALL
)
_SO360_SNIPPET_RE = re.compile(r'<p\s+class="res-desc"[^>]*>(.*?)</p>', re.DOTALL)

ENGINE_360 = SearchEngine(
    name="360", label="360搜索",
    search_url="https://www.so.com/s",
    search_params_fn=lambda q, n: {"q": q},
    parse_fn=_make_standard_parser(_SO360_BLOCK_RE, _SO360_TITLE_RE, _SO360_SNIPPET_RE),
)

# 搜狗
_SOGOU_BLOCK_RE = re.compile(r'<div\s+class="rb"[^>]*>(.*?)</div>\s*</div>', re.DOTALL)
_SOGOU_TITLE_RE = re.compile(
    r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>\s*</h3>', re.DOTALL
)
_SOGOU_SNIPPET_RE = re.compile(
    r'<(?:div\s+class="ft"|p\s+class="str_info"[^>]*|div[^>]*class="space"[^>]*)>(.*?)</(?:div|p)>',
    re.DOTALL,
)

ENGINE_SOGOU = SearchEngine(
    name="sogou", label="搜狗",
    search_url="https://www.sogou.com/web",
    search_params_fn=lambda q, n: {"query": q},
    parse_fn=_make_standard_parser(_SOGOU_BLOCK_RE, _SOGOU_TITLE_RE, _SOGOU_SNIPPET_RE),
)

# 神马
_SHENMA_BLOCK_RE = re.compile(r'<div\s+class="card-wrap"[^>]*>(.*?)</div>\s*</div>', re.DOTALL)
_SHENMA_TITLE_RE = re.compile(
    r'<a[^>]*href="([^"]+)"[^>]*class="[^"]*title[^"]*"[^>]*>(.+?)</a>', re.DOTALL
)
_SHENMA_TITLE_ALT_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>', re.DOTALL)
_SHENMA_SNIPPET_RE = re.compile(
    r'<(?:div|p|span)[^>]*class="[^"]*(?:abstract|summary|desc|info)[^"]*"[^>]*>(.+?)</(?:div|p|span)>',
    re.DOTALL,
)


def _parse_shenma(html: str, max_results: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    blocks = _SHENMA_BLOCK_RE.findall(html)
    if not blocks:
        alt_re = re.compile(r'<div\s+class="card"[^>]*>(.*?)</div>', re.DOTALL)
        blocks = alt_re.findall(html)
    for block in blocks[:max_results]:
        tm = _SHENMA_TITLE_RE.search(block) or _SHENMA_TITLE_ALT_RE.search(block)
        if not tm:
            continue
        url = tm.group(1)
        title = _strip_html(tm.group(2))
        snippet = ""
        sm = _SHENMA_SNIPPET_RE.search(block)
        if sm:
            snippet = _strip_html(sm.group(1))
        if title:
            results.append({"title": title, "href": url, "body": snippet})
    return results


ENGINE_SHENMA = SearchEngine(
    name="shenma", label="神马",
    search_url="https://m.sm.cn/s",
    search_params_fn=lambda q, n: {"q": q},
    parse_fn=_parse_shenma,
    extra_headers={
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; SM-G9750) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Mobile Safari/537.36"
        )
    },
)

# 头条
_TOUTIAO_BLOCK_RE = re.compile(
    r'<(?:div|li)[^>]*class="[^"]*(?:result|item|article)[^"]*"[^>]*>(.+?)</(?:div|li)>',
    re.DOTALL,
)
_TOUTIAO_TITLE_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>', re.DOTALL)
_TOUTIAO_SNIPPET_RE = re.compile(
    r'<(?:p|span|div)[^>]*class="[^"]*(?:abstract|desc|snippet|content)[^"]*"[^>]*>(.+?)</(?:p|span|div)>',
    re.DOTALL,
)


def _parse_toutiao(html: str, max_results: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    blocks = _TOUTIAO_BLOCK_RE.findall(html)
    for block in blocks[:max_results]:
        tm = _TOUTIAO_TITLE_RE.search(block)
        if not tm:
            continue
        url = tm.group(1)
        title = _strip_html(tm.group(2))
        if len(title) < 3:
            continue
        snippet = ""
        sm = _TOUTIAO_SNIPPET_RE.search(block)
        if sm:
            snippet = _strip_html(sm.group(1))
        results.append({"title": title, "href": url, "body": snippet})
    return results


ENGINE_TOUTIAO = SearchEngine(
    name="toutiao", label="头条",
    search_url="https://so.toutiao.com/search",
    search_params_fn=lambda q, n: {"keyword": q},
    parse_fn=_parse_toutiao,
)

# ── 引擎注册表 ────────────────────────────────────────────

_SEARCH_ENGINES: list[SearchEngine] = [
    ENGINE_BING,
    ENGINE_BAIDU,
    ENGINE_360,
    ENGINE_SOGOU,
    ENGINE_SHENMA,
    ENGINE_TOUTIAO,
]


def _engine_search(engine: SearchEngine, query: str, max_results: int) -> tuple[str, list[dict[str, Any]]]:
    """执行单个搜索引擎查询，返回 (引擎名, 结果列表)。"""
    try:
        params = engine.search_params_fn(query, max_results)
        html = _fetch_html(engine.search_url, params, headers=engine.extra_headers)
        if not html:
            return (engine.name, [])
        results = engine.parse_fn(html, max_results)
        logger.debug(f"[{engine.label}] returned {len(results)} results")
        return (engine.name, results)
    except Exception as exc:
        logger.warning(f"[{engine.label}] search failed: {type(exc).__name__}: {exc}")
        return (engine.name, [])


def _parallel_search(query: str, max_results: int, max_total: int = 8) -> list[dict[str, Any]]:
    """并行搜索所有引擎，合并去重取前 N 条。"""
    with ThreadPoolExecutor(max_workers=len(_SEARCH_ENGINES)) as executor:
        futures = {
            executor.submit(_engine_search, eng, query, max_results): eng
            for eng in _SEARCH_ENGINES
        }

        engine_results: list[tuple[str, list[dict[str, Any]]]] = []
        for future in as_completed(futures, timeout=15):
            try:
                engine_results.append(future.result())
            except Exception as exc:
                eng = futures[future]
                logger.warning(f"[{eng.label}] search timeout/error: {type(exc).__name__}")

    # 合并去重
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for engine_name, results in engine_results:
        for r in results:
            href = (r.get("href") or r.get("link") or "").strip().rstrip("/")
            if not href or href in seen:
                continue
            seen.add(href)
            r["_engine"] = engine_name
            merged.append(r)
            if len(merged) >= max_total:
                return merged
    return merged


# ── DDG 兜底 ──────────────────────────────────────────────

def _ddg_web_search(query: str, max_results: int, region: str, safesearch: str) -> list[dict]:
    from ddgs import DDGS
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results, region=region, safesearch=safesearch))


def _ddg_news_search(
    query: str, max_results: int, region: str, safesearch: str, timelimit: str | None,
) -> list[dict]:
    from ddgs import DDGS
    with DDGS() as ddgs:
        return ddgs.news(
            query, max_results=max_results, region=region, safesearch=safesearch,
            timelimit=timelimit,
        )


# ── MCP 服务器 ────────────────────────────────────────────

mcp = FastMCP(
    name="web-search",
    instructions="""Web Search MCP Server — 六引擎并行搜索 (Bing/百度/360/搜狗/神马/头条)

可用工具：
- web_search: 搜索网页，返回标题、链接和摘要
- news_search: 搜索新闻，返回最新新闻文章

搜索引擎：6引擎同时搜索，合并去重取前8条。DuckDuckGo作为兜底。
""",
)


def _format_web_results(results: list) -> str:
    if not results:
        return "未找到相关结果"
    output = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "无标题")
        url = r.get("href", r.get("link", ""))
        body = r.get("body", r.get("snippet", ""))
        engine_tag = f" [{r.get('_engine', '')}]" if r.get("_engine") else ""
        output.append(f"**{i}. {title}**{engine_tag}\n{url}\n{body}\n")
    return "\n".join(output)


def _format_news_results(results: list) -> str:
    if not results:
        return "未找到相关新闻"
    output = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "无标题")
        url = r.get("url", r.get("link", ""))
        body = r.get("body", r.get("excerpt", ""))
        date = r.get("date", "")
        source = r.get("source", "")
        engine_tag = f" [{r.get('_engine', '')}]" if r.get("_engine") else ""
        header = f"**{i}. {title}**"
        if source or date:
            header += f" ({source} {date})"
        header += engine_tag
        output.append(f"{header}\n{url}\n{body}\n")
    return "\n".join(output)


@mcp.tool()
def web_search(
    query: str, max_results: int = 5, region: str = "wt-wt", safesearch: str = "moderate"
) -> str:
    """
    Search the web — 6 engines parallel (Bing/Baidu/360/Sogou/Shenma/Toutiao), DDG fallback.

    Args:
        query: Search query string
        max_results: Maximum number of results (default: 5, max: 20)
        region: Region code (DDG fallback only)
        safesearch: Safe search level (DDG fallback only)

    Returns:
        Formatted search results with title, URL, snippet, and source engine tag
    """
    max_results = min(max(1, max_results), 20)

    try:
        results = _parallel_search(query, max_results)
        if results:
            logger.info("Multi-engine search returned %d results for: %s", len(results), query)
            return _format_web_results(results)
    except Exception as e:
        logger.warning(f"Multi-engine search failed, falling back to DDG: {type(e).__name__}: {e}")

    try:
        from ddgs import DDGS  # noqa: F401
    except ImportError:
        from openakita.tools._import_helper import import_or_hint
        return f"错误：{import_or_hint('ddgs')}"

    try:
        results = _ddg_web_search(query, max_results, region, safesearch)
        return _format_web_results(results)
    except Exception as e:
        logger.error(f"DDG web search failed: {type(e).__name__}: {e}")
        return f"搜索失败 (所有引擎): {type(e).__name__}: {e}"


@mcp.tool()
def news_search(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timelimit: str | None = None,
) -> str:
    """
    Search news — 6 engines parallel (Bing/Baidu/360/Sogou/Shenma/Toutiao), DDG fallback.

    Args:
        query: Search query string
        max_results: Maximum number of results (default: 5, max: 20)
        region: Region code (DDG fallback only)
        safesearch: Safe search level (DDG fallback only)
        timelimit: Time limit (DDG fallback only, "d"/"w"/"m")

    Returns:
        Formatted news results with title, source, date, URL, and source engine tag
    """
    max_results = min(max(1, max_results), 20)

    try:
        results = _parallel_search(query, max_results)
        if results:
            logger.info("Multi-engine news search returned %d results for: %s", len(results), query)
            return _format_news_results(results)
    except Exception as e:
        logger.warning(f"Multi-engine news search failed, falling back to DDG: {type(e).__name__}: {e}")

    try:
        from ddgs import DDGS  # noqa: F401
    except ImportError:
        from openakita.tools._import_helper import import_or_hint
        return f"错误：{import_or_hint('ddgs')}"

    try:
        results = _ddg_news_search(query, max_results, region, safesearch, timelimit)
        return _format_news_results(results)
    except Exception as e:
        logger.error(f"DDG news search failed: {type(e).__name__}: {e}")
        return f"新闻搜索失败 (所有引擎): {type(e).__name__}: {e}"


if __name__ == "__main__":
    mcp.run()
