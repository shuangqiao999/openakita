"""
Web Search MCP 服务器

六引擎并行搜索：Bing / Baidu / 360 / Sogou / Shenma / Toutiao
- 每个引擎独立 10s 超时 + 重试 1 次（间隔 0.5s）
- 全部引擎失败 → DDG 兜底 → 仍空则返回明确错误
- URL/标题合法性校验
- 结构化日志

启动方式：
    python -m openakita.mcp_servers.web_search
"""

import json
import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

_ENGINE_TIMEOUT = 10.0
_RETRY_DELAY = 0.5
_MAX_RETRIES = 1
_MERGE_LIMIT = 8

_UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_UA_MOBILE = (
    "Mozilla/5.0 (Linux; Android 10; SM-G9750) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)

_RE_STRIP = re.compile(r"<[^>]+>")
_RE_ENTITY = re.compile(r"&[a-z]+;")
_RE_WS = re.compile(r"\s+")
_RE_VALID_URL = re.compile(r"^https?://")


def _strip_html(text: str) -> str:
    text = _RE_STRIP.sub(" ", text)
    text = _RE_ENTITY.sub(" ", text)
    return _RE_WS.sub(" ", text).strip()


def _is_valid_url(url: str) -> bool:
    if not url or not _RE_VALID_URL.match(url):
        return False
    try:
        parsed = urlparse(url)
        return bool(parsed.scheme and parsed.netloc)
    except Exception:
        return False


def _is_valid_result(r: dict[str, Any]) -> bool:
    title = (r.get("title") or "").strip()
    url = (r.get("href") or r.get("link") or "").strip()
    return bool(title) and _is_valid_url(url)


def _fetch_html(url: str, params: dict, *, headers: dict | None = None, timeout: float = _ENGINE_TIMEOUT) -> str | None:
    import httpx
    default_headers = {
        "User-Agent": _UA_DESKTOP,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if headers:
        default_headers.update(headers)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, params=params, headers=default_headers)
            resp.raise_for_status()
            return resp.text
    except Exception:
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


# ── 搜索引擎定义 ──────────────────────────────────────────

@dataclass
class SearchEngine:
    name: str
    label: str
    search_url: str
    search_params_fn: Callable[[str, int], dict]
    parse_fn: Callable[[str, int], list[dict[str, Any]]]
    extra_headers: dict | None = None
    ua_override: str | None = None


def _make_standard_parser(
    block_re: re.Pattern, title_re: re.Pattern, snippet_re: re.Pattern,
    *, url_group: int = 1, title_group: int = 2, snippet_group: int = 1,
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


# Bing
_BING_BLOCK_RE = re.compile(r'<li\s+class="b_algo"[^>]*>(.*?)</li>', re.DOTALL)
_BING_TITLE_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>\s*</h2>', re.DOTALL)
_BING_SNIPPET_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL)
ENGINE_BING = SearchEngine("bing", "Bing", "https://cn.bing.com/search",
    lambda q, n: {"q": q, "count": n},
    _make_standard_parser(_BING_BLOCK_RE, _BING_TITLE_RE, _BING_SNIPPET_RE))

# 百度
_BAIDU_BLOCK_RE = re.compile(r'<div[^>]*class="(?:result|c-container)[^"]*"[^>]*cachable[^>]*>(.+?)</div>\s*(?:</div>)?', re.DOTALL)
_BAIDU_BLOCK_ALT_RE = re.compile(r'<div[^>]*class="(?:result|c-container)[^"]*"[^>]*>(.+?)(?:</div>\s*</div>|</div>\s*$|<div[^>]*class="(?:result|c-container))', re.DOTALL)
_BAIDU_TITLE_RE = re.compile(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>\s*</h3>', re.DOTALL)
_BAIDU_SNIPPET_RE = re.compile(r'<(?:span[^>]*class="[^"]*content-right[^"]*"|div[^>]*class="c-abstract"[^>]*|span[^>]*class="c-color"[^>]*)>(.+?)</(?:span|div)>', re.DOTALL)
_BAIDU_SNIPPET_ALT_RE = re.compile(r'<(?:span|div|p)[^>]*class="[^"]*abstract[^"]*"[^>]*>(.*?)</(?:span|div|p)>', re.DOTALL)

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

ENGINE_BAIDU = SearchEngine("baidu", "百度", "https://www.baidu.com/s",
    lambda q, n: {"wd": q, "rn": str(n)}, _parse_baidu, extra_headers={"Referer": "https://www.baidu.com/"})

# 360
_SO360_BLOCK_RE = re.compile(r'<li\s+class="res-list"[^>]*>(.*?)</li>', re.DOTALL)
_SO360_TITLE_RE = re.compile(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>\s*</h3>', re.DOTALL)
_SO360_SNIPPET_RE = re.compile(r'<p\s+class="res-desc"[^>]*>(.*?)</p>', re.DOTALL)
ENGINE_360 = SearchEngine("360", "360搜索", "https://www.so.com/s",
    lambda q, n: {"q": q}, _make_standard_parser(_SO360_BLOCK_RE, _SO360_TITLE_RE, _SO360_SNIPPET_RE))

# 搜狗
_SOGOU_BLOCK_RE = re.compile(r'<div\s+class="rb"[^>]*>(.*?)</div>\s*</div>', re.DOTALL)
_SOGOU_TITLE_RE = re.compile(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>\s*</h3>', re.DOTALL)
_SOGOU_SNIPPET_RE = re.compile(r'<(?:div\s+class="ft"|p\s+class="str_info"[^>]*|div[^>]*class="space"[^>]*)>(.*?)</(?:div|p)>', re.DOTALL)
ENGINE_SOGOU = SearchEngine("sogou", "搜狗", "https://www.sogou.com/web",
    lambda q, n: {"query": q}, _make_standard_parser(_SOGOU_BLOCK_RE, _SOGOU_TITLE_RE, _SOGOU_SNIPPET_RE))

# 神马
_SHENMA_BLOCK_RE = re.compile(r'<div\s+class="card-wrap"[^>]*>(.*?)</div>\s*</div>', re.DOTALL)
_SHENMA_TITLE_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*class="[^"]*title[^"]*"[^>]*>(.+?)</a>', re.DOTALL)
_SHENMA_TITLE_ALT_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>', re.DOTALL)
_SHENMA_SNIPPET_RE = re.compile(r'<(?:div|p|span)[^>]*class="[^"]*(?:abstract|summary|desc|info)[^"]*"[^>]*>(.+?)</(?:div|p|span)>', re.DOTALL)

def _parse_shenma(html: str, max_results: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    blocks = _SHENMA_BLOCK_RE.findall(html)
    if not blocks:
        blocks = re.compile(r'<div\s+class="card"[^>]*>(.*?)</div>', re.DOTALL).findall(html)
    for block in blocks[:max_results]:
        tm = _SHENMA_TITLE_RE.search(block) or _SHENMA_TITLE_ALT_RE.search(block)
        if not tm:
            continue
        title = _strip_html(tm.group(2))
        snippet = ""
        sm = _SHENMA_SNIPPET_RE.search(block)
        if sm:
            snippet = _strip_html(sm.group(1))
        if title:
            results.append({"title": title, "href": tm.group(1), "body": snippet})
    return results

ENGINE_SHENMA = SearchEngine("shenma", "神马", "https://m.sm.cn/s",
    lambda q, n: {"q": q}, _parse_shenma, ua_override=_UA_MOBILE)

# 头条
_TOUTIAO_BLOCK_RE = re.compile(r'<(?:div|li)[^>]*class="[^"]*(?:result|item|article)[^"]*"[^>]*>(.+?)</(?:div|li)>', re.DOTALL)
_TOUTIAO_TITLE_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>', re.DOTALL)
_TOUTIAO_SNIPPET_RE = re.compile(r'<(?:p|span|div)[^>]*class="[^"]*(?:abstract|desc|snippet|content)[^"]*"[^>]*>(.+?)</(?:p|span|div)>', re.DOTALL)

def _parse_toutiao(html: str, max_results: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    blocks = _TOUTIAO_BLOCK_RE.findall(html)
    for block in blocks[:max_results]:
        tm = _TOUTIAO_TITLE_RE.search(block)
        if not tm:
            continue
        title = _strip_html(tm.group(2))
        if len(title) < 3:
            continue
        snippet = ""
        sm = _TOUTIAO_SNIPPET_RE.search(block)
        if sm:
            snippet = _strip_html(sm.group(1))
        results.append({"title": title, "href": tm.group(1), "body": snippet})
    return results

ENGINE_TOUTIAO = SearchEngine("toutiao", "头条", "https://so.toutiao.com/search",
    lambda q, n: {"keyword": q}, _parse_toutiao)

_SEARCH_ENGINES: list[SearchEngine] = [
    ENGINE_BING, ENGINE_BAIDU, ENGINE_360,
    ENGINE_SOGOU, ENGINE_SHENMA, ENGINE_TOUTIAO,
]


# ── 单引擎搜索 + 重试 ─────────────────────────────────────

def _engine_search_once(engine: SearchEngine, query: str, max_results: int) -> list[dict[str, Any]]:
    params = engine.search_params_fn(query, max_results)
    extra = {}
    if engine.extra_headers:
        extra["headers"] = engine.extra_headers
    html = _fetch_html(engine.search_url, params, timeout=_ENGINE_TIMEOUT, **extra)
    if not html:
        return []
    try:
        results = engine.parse_fn(html, max_results)
    except Exception as exc:
        logger.warning(f"[{engine.label}] parse failed: {type(exc).__name__}: {exc}")
        return []
    return [r for r in results if _is_valid_result(r)]


def _engine_search_with_retry(engine: SearchEngine, query: str, max_results: int) -> tuple[str, list[dict[str, Any]]]:
    t0 = time.perf_counter()
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            results = _engine_search_once(engine, query, max_results)
            elapsed = (time.perf_counter() - t0) * 1000
            if results:
                logger.info("[%s] %d results in %.0fms (attempt %d/2) query=%s",
                            engine.label, len(results), elapsed, attempt + 1, query[:60])
            return (engine.name, results)
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                logger.info("[%s] attempt %d failed (%s), retrying...", engine.label, attempt + 1, type(exc).__name__)
                time.sleep(_RETRY_DELAY)

    elapsed = (time.perf_counter() - t0) * 1000
    logger.warning("[%s] all %d attempts failed (%.0fms): %s", engine.label, _MAX_RETRIES + 1, elapsed, last_exc)
    return (engine.name, [])


def _parallel_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """并行搜索所有引擎（含每引擎重试），合并去重。"""
    t0 = time.perf_counter()

    with ThreadPoolExecutor(max_workers=len(_SEARCH_ENGINES)) as executor:
        futures = {executor.submit(_engine_search_with_retry, eng, query, max_results): eng
                   for eng in _SEARCH_ENGINES}

        engine_results: list[tuple[str, list[dict[str, Any]]]] = []
        for future in as_completed(futures, timeout=_ENGINE_TIMEOUT + 3):
            try:
                engine_results.append(future.result())
            except (FutureTimeoutError, Exception) as exc:
                eng = futures[future]
                logger.warning(f"[{eng.label}] search timeout/error: {type(exc).__name__}")

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
            if len(merged) >= _MERGE_LIMIT:
                break
        if len(merged) >= _MERGE_LIMIT:
            break

    total_ms = (time.perf_counter() - t0) * 1000
    logger.info("多引擎并行搜索 %d 条结果，耗时 %.0fms", len(merged), total_ms)
    return merged


# ── DDG 兜底 ──────────────────────────────────────────────

def _ddg_web_search(query: str, max_results: int, region: str, safesearch: str) -> list[dict]:
    from ddgs import DDGS
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results, region=region, safesearch=safesearch))


def _ddg_news_search(query: str, max_results: int, region: str, safesearch: str, timelimit: str | None) -> list[dict]:
    from ddgs import DDGS
    with DDGS() as ddgs:
        return ddgs.news(query, max_results=max_results, region=region, safesearch=safesearch, timelimit=timelimit)


# ── MCP 服务器 ────────────────────────────────────────────

mcp = FastMCP(
    name="web-search",
    instructions="""Web Search MCP Server — 六引擎并行搜索（Bing/百度/360/搜狗/神马/头条）

特性：
- 单引擎 10s 超时 + 重试 1 次（网络瞬断自动恢复）
- 合并去重取前 8 条，URL/标题合法性校验
- 全引擎失败 → DDG 兜底 → 仍空则返回明确错误 JSON

可用工具：web_search / news_search
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


def _all_failed_json(kind: str) -> str:
    label = "新闻" if kind == "news" else "网页"
    return json.dumps({
        "success": False,
        "message": f"所有{label}搜索引擎（Bing/百度/360/搜狗/神马/头条 + DDG）均无结果。请检查网络后重试。",
        "results": [],
    }, ensure_ascii=False)


@mcp.tool()
def web_search(query: str, max_results: int = 5, region: str = "wt-wt", safesearch: str = "moderate") -> str:
    """Search the web — 6 engines parallel with retry, DDG fallback."""
    max_results = min(max(1, max_results), 20)

    try:
        results = _parallel_search(query, max_results)
        if results:
            return _format_web_results(results)
    except Exception as e:
        logger.warning(f"Multi-engine search failed, trying DDG: {type(e).__name__}: {e}")

    try:
        from ddgs import DDGS  # noqa: F401
    except ImportError:
        from openakita.tools._import_helper import import_or_hint
        return f"错误：{import_or_hint('ddgs')}"

    try:
        results = _ddg_web_search(query, max_results, region, safesearch)
        if results:
            for r in results:
                r["_engine"] = "ddg"
            return _format_web_results(results[:8])
    except Exception as e:
        logger.error(f"DDG web search failed: {type(e).__name__}: {e}")

    return _all_failed_json("web")


@mcp.tool()
def news_search(query: str, max_results: int = 5, region: str = "wt-wt", safesearch: str = "moderate", timelimit: str | None = None) -> str:
    """Search news — 6 engines parallel with retry, DDG fallback."""
    max_results = min(max(1, max_results), 20)

    try:
        results = _parallel_search(query, max_results)
        if results:
            return _format_news_results(results)
    except Exception as e:
        logger.warning(f"Multi-engine news search failed, trying DDG: {type(e).__name__}: {e}")

    try:
        from ddgs import DDGS  # noqa: F401
    except ImportError:
        from openakita.tools._import_helper import import_or_hint
        return f"错误：{import_or_hint('ddgs')}"

    try:
        results = _ddg_news_search(query, max_results, region, safesearch, timelimit)
        if results:
            for r in results:
                r["_engine"] = "ddg"
            return _format_news_results(results[:8])
    except Exception as e:
        logger.error(f"DDG news search failed: {type(e).__name__}: {e}")

    return _all_failed_json("news")


if __name__ == "__main__":
    mcp.run()
