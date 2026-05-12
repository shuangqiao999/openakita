"""
Web Search 处理器

六引擎并行搜索：Bing / Baidu / 360 / Sogou / Shenma / Toutiao
所有引擎同时发起搜索 → 合并结果 → 按URL去重 → 取前8条
DuckDuckGo 作为最后兜底（所有国内引擎均失败时启用）
"""

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...config import settings

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
    """通用 HTTP GET 获取 HTML。"""
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
    """百度搜索结果中的 URL 是重定向链接，提取真实目标 URL。"""
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


# ── 搜索引擎实现 ──────────────────────────────────────────

@dataclass
class SearchEngine:
    """单个搜索引擎的描述。"""
    name: str
    label: str  # 中文显示名
    search_url: str
    search_params_fn: Callable[[str, int], dict]
    parse_fn: Callable[[str, int], list[dict[str, Any]]]
    extra_headers: dict | None = None


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
    """通用解析器工厂：用三个正则提取搜索结果的标题/URL/摘要。"""
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


# ── Bing ─────────────────────────────────────────────────

_BING_BLOCK_RE = re.compile(r'<li\s+class="b_algo"[^>]*>(.*?)</li>', re.DOTALL)
_BING_TITLE_RE = re.compile(
    r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>\s*</h2>', re.DOTALL
)
_BING_SNIPPET_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL)

_bing_parser = _make_standard_parser(_BING_BLOCK_RE, _BING_TITLE_RE, _BING_SNIPPET_RE)

ENGINE_BING = SearchEngine(
    name="bing",
    label="Bing",
    search_url="https://cn.bing.com/search",
    search_params_fn=lambda q, n: {"q": q, "count": n},
    parse_fn=_bing_parser,
)

# ── 百度 ─────────────────────────────────────────────────

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


ENGINE_BAIDU = SearchEngine(
    name="baidu",
    label="百度",
    search_url="https://www.baidu.com/s",
    search_params_fn=lambda q, n: {"wd": q, "rn": str(n)},
    parse_fn=_parse_baidu,
    extra_headers={"Referer": "https://www.baidu.com/"},
)

# ── 360 搜索 ──────────────────────────────────────────────

_SO360_BLOCK_RE = re.compile(r'<li\s+class="res-list"[^>]*>(.*?)</li>', re.DOTALL)
_SO360_TITLE_RE = re.compile(
    r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>\s*</h3>', re.DOTALL
)
_SO360_SNIPPET_RE = re.compile(r'<p\s+class="res-desc"[^>]*>(.*?)</p>', re.DOTALL)
_SO360_SNIPPET_ALT_RE = re.compile(r'<div\s+class="res-rich"[^>]*>(.*?)</div>', re.DOTALL)

_so360_parser = _make_standard_parser(
    _SO360_BLOCK_RE, _SO360_TITLE_RE, _SO360_SNIPPET_RE
)

ENGINE_360 = SearchEngine(
    name="360",
    label="360搜索",
    search_url="https://www.so.com/s",
    search_params_fn=lambda q, n: {"q": q},
    parse_fn=_so360_parser,
)

# ── 搜狗 ─────────────────────────────────────────────────

_SOGOU_BLOCK_RE = re.compile(r'<div\s+class="rb"[^>]*>(.*?)</div>\s*</div>', re.DOTALL)
_SOGOU_TITLE_RE = re.compile(
    r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>\s*</h3>', re.DOTALL
)
_SOGOU_SNIPPET_RE = re.compile(
    r'<(?:div\s+class="ft"|p\s+class="str_info"[^>]*|div[^>]*class="space"[^>]*)>(.*?)</(?:div|p)>',
    re.DOTALL,
)

_sogou_parser = _make_standard_parser(_SOGOU_BLOCK_RE, _SOGOU_TITLE_RE, _SOGOU_SNIPPET_RE)

ENGINE_SOGOU = SearchEngine(
    name="sogou",
    label="搜狗",
    search_url="https://www.sogou.com/web",
    search_params_fn=lambda q, n: {"query": q},
    parse_fn=_sogou_parser,
)

# ── 神马搜索 ──────────────────────────────────────────────

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
    name="shenma",
    label="神马",
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

# ── 头条搜索 ──────────────────────────────────────────────

_TOUTIAO_BLOCK_RE = re.compile(
    r'<(?:div|li)[^>]*class="[^"]*(?:result|item|article)[^"]*"[^>]*>(.+?)</(?:div|li)>',
    re.DOTALL,
)
_TOUTIAO_TITLE_RE = re.compile(
    r'<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>', re.DOTALL
)
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
    name="toutiao",
    label="头条",
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


def _sync_engine_search(engine: SearchEngine, query: str, max_results: int) -> list[dict[str, Any]]:
    """同步执行单个搜索引擎查询（在独立线程中调用）。"""
    try:
        params = engine.search_params_fn(query, max_results)
        html = _fetch_html(engine.search_url, params, headers=engine.extra_headers)
        if not html:
            return []
        results = engine.parse_fn(html, max_results)
        logger.debug(f"[{engine.label}] returned {len(results)} results for: {query}")
        return results
    except Exception as exc:
        logger.warning(f"[{engine.label}] search failed: {type(exc).__name__}: {exc}")
        return []


def _merge_dedup_results(
    engine_results: list[tuple[str, list[dict[str, Any]]]],
    max_total: int = 8,
) -> list[dict[str, Any]]:
    """合并多引擎结果，按 URL 去重，取前 N 条。"""
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


def _format_engine_summary(merged: list[dict[str, Any]]) -> str:
    """生成搜索引擎命中统计字符串。"""
    from collections import Counter
    engine_hits = Counter(r.get("_engine", "?") for r in merged)
    parts = []
    for eng in _SEARCH_ENGINES:
        if eng.name in engine_hits:
            parts.append(f"{eng.label}({engine_hits[eng.name]}条)")
    return "、".join(parts) if parts else "0条"


# ── DDG 搜索（兜底）───────────────────────────────────────


def _sync_ddg_web_search(
    query: str, max_results: int, region: str, safesearch: str
) -> list[dict[str, Any]]:
    from ddgs import DDGS
    with DDGS() as ddgs:
        return ddgs.text(query, max_results=max_results, region=region, safesearch=safesearch)


def _sync_ddg_news_search(
    query: str, max_results: int, region: str, safesearch: str, timelimit: str | None,
) -> list[dict[str, Any]]:
    from ddgs import DDGS
    with DDGS() as ddgs:
        return ddgs.news(
            query, max_results=max_results, region=region, safesearch=safesearch,
            timelimit=timelimit,
        )


# ── 安全过滤 ──────────────────────────────────────────────

_UNSAFE_SEARCH_KEYWORDS = (
    "色情", "情色", "裸聊", "裸露", "约炮", "女优", "网黄", "无码视频",
    "无码", "强奸", "自慰", "阴茎", "阳具", "必撸",
    "porn", "xxx", "xvideo", "onlyfans",
)
_UNSAFE_DOMAIN_RE = re.compile(
    r"(?:^|\.)(" r"porn|xvideos|xnxx|xhamster|onlyfans|jav|sex|adult|noduown" r")\.",
    re.IGNORECASE,
)


def _result_text(result: dict[str, Any]) -> str:
    return " ".join(
        str(result.get(key, "") or "")
        for key in ("title", "href", "link", "url", "body", "snippet", "excerpt", "source")
    )


def _is_unsafe_search_result(result: dict[str, Any]) -> bool:
    text = _result_text(result).lower()
    if not text:
        return False
    if _UNSAFE_DOMAIN_RE.search(text):
        return True
    return any(keyword in text for keyword in _UNSAFE_SEARCH_KEYWORDS)


def _filter_search_results(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    filtered = [r for r in results if not _is_unsafe_search_result(r)]
    return filtered, len(results) - len(filtered)


def _resolve_attempt_timeout(params: dict[str, Any]) -> float:
    raw = params.get("timeout_seconds", settings.web_search_attempt_timeout_seconds)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return max(0.0, float(settings.web_search_attempt_timeout_seconds or 0))


async def _run_search_attempt(func, *, timeout_seconds: float, **kwargs) -> list[dict[str, Any]]:
    task = asyncio.to_thread(func, **kwargs)
    if timeout_seconds <= 0:
        return await task
    return await asyncio.wait_for(task, timeout=timeout_seconds)


def _format_search_timeout(kind: str, timeout_seconds: float) -> str:
    label = "新闻搜索" if kind == "news" else "网页搜索"
    timeout_display = f"{timeout_seconds:g}"
    return (
        f"{label}本次等待超过 {timeout_display} 秒，已先跳过这个外部搜索源。"
        "这不代表任务失败：请优先基于已获得的信息继续完成用户目标；"
        "如果证据不足，可以换更具体的关键词、改用 web_fetch/browser 访问权威来源，"
        "或在结果中标注哪些内容尚未联网验证。不要反复用完全相同的查询空转。"
    )


# ── Handler 类 ────────────────────────────────────────────


class WebSearchHandler:
    """多引擎并行 Web Search 处理器"""

    TOOLS = ["web_search", "news_search"]

    def __init__(self, agent: Any = None):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "web_search":
            return await self._web_search(params)
        elif tool_name == "news_search":
            return await self._news_search(params)
        else:
            return f"Unknown web search tool: {tool_name}"

    async def _web_search(self, params: dict[str, Any]) -> str:
        """搜索网页 — 六引擎并行，DDG 兜底"""
        query = params.get("query", "")
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, params.get("max_results", 5)), 20)
        region = params.get("region", "wt-wt")
        safesearch = params.get("safesearch", "moderate")
        timeout_seconds = _resolve_attempt_timeout(params)

        # ① 并行搜索所有国内引擎
        tasks = [
            _run_search_attempt(
                _sync_engine_search,
                timeout_seconds=timeout_seconds,
                engine=eng,
                query=query,
                max_results=max_results,
            )
            for eng in _SEARCH_ENGINES
        ]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        # 收集各引擎结果
        engine_results: list[tuple[str, list[dict[str, Any]]]] = []
        for eng, results in zip(_SEARCH_ENGINES, all_results, strict=True):
            if isinstance(results, Exception):
                logger.warning(f"[{eng.label}] search exception: {type(results).__name__}")
                continue
            if results:
                engine_results.append((eng.name, results))

        # 合并去重
        merged = _merge_dedup_results(engine_results)
        if merged:
            summary = _format_engine_summary(merged)
            logger.info(
                "六引擎并行搜索「%s」→ 共 %d 条（%s）", query, len(merged), summary
            )
            return self._format_web_results(merged)

        # ② 兜底：DuckDuckGo
        logger.info("All Chinese engines returned no results, falling back to DDG: %s", query)
        try:
            from ddgs import DDGS  # noqa: F401
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            return f"错误：{import_or_hint('ddgs')}"

        try:
            results = await _run_search_attempt(
                _sync_ddg_web_search,
                timeout_seconds=timeout_seconds,
                query=query,
                max_results=max_results,
                region=region,
                safesearch=safesearch,
            )
            return self._format_web_results(results)
        except TimeoutError:
            logger.warning("DDG web search timed out: %s", query)
            return _format_search_timeout("web", timeout_seconds)
        except Exception as e:
            logger.error(f"All search engines failed: {type(e).__name__}: {e}")
            return (
                "搜索暂时不可用（所有搜索引擎均无法访问）。"
                "请直接告知用户\"当前无法联网搜索\"，建议稍后重试或改用其他工具。"
            )

    async def _news_search(self, params: dict[str, Any]) -> str:
        """搜索新闻 — 六引擎并行，DDG 兜底（注：news 搜索复用网页搜索引擎）"""
        query = params.get("query", "")
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, params.get("max_results", 5)), 20)
        region = params.get("region", "wt-wt")
        safesearch = params.get("safesearch", "moderate")
        timelimit = params.get("timelimit")
        timeout_seconds = _resolve_attempt_timeout(params)

        # ① 并行搜索所有国内引擎（news 关键词前缀增强新闻感知）
        news_query = query
        tasks = [
            _run_search_attempt(
                _sync_engine_search,
                timeout_seconds=timeout_seconds,
                engine=eng,
                query=news_query,
                max_results=max_results,
            )
            for eng in _SEARCH_ENGINES
        ]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        engine_results: list[tuple[str, list[dict[str, Any]]]] = []
        for eng, results in zip(_SEARCH_ENGINES, all_results, strict=True):
            if isinstance(results, Exception):
                logger.warning(f"[{eng.label}] news search exception: {type(results).__name__}")
                continue
            if results:
                engine_results.append((eng.name, results))

        merged = _merge_dedup_results(engine_results)
        if merged:
            summary = _format_engine_summary(merged)
            logger.info(
                "六引擎并行新闻搜索「%s」→ 共 %d 条（%s）", query, len(merged), summary
            )
            return self._format_news_results(merged)

        # ② 兜底：DuckDuckGo news
        logger.info("All Chinese engines returned no news, falling back to DDG: %s", query)
        try:
            from ddgs import DDGS  # noqa: F401
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            return f"错误：{import_or_hint('ddgs')}"

        try:
            results = await _run_search_attempt(
                _sync_ddg_news_search,
                timeout_seconds=timeout_seconds,
                query=query,
                max_results=max_results,
                region=region,
                safesearch=safesearch,
                timelimit=timelimit,
            )
            return self._format_news_results(results)
        except TimeoutError:
            logger.warning("DDG news search timed out: %s", query)
            return _format_search_timeout("news", timeout_seconds)
        except Exception as e:
            logger.error(f"All news search engines failed: {type(e).__name__}: {e}")
            return (
                "新闻搜索暂时不可用（所有搜索引擎均无法访问）。"
                "请直接告知用户\"当前无法联网搜索\"，建议稍后重试或改用其他工具。"
            )

    @staticmethod
    def _format_web_results(results: list) -> str:
        if not results:
            return "未找到相关结果"

        safe_results, hidden_count = _filter_search_results(results)
        if not safe_results:
            return (
                f"搜索返回了 {len(results)} 条结果，但结果内容质量不可靠或可能触发平台安全审核，"
                "已隐藏。请换用更具体关键词、web_fetch、浏览器或权威来源继续获取证据；"
                "如果当前确实没有可验证信息，请明确说明无法联网验证，不要编造结果。"
            )

        output = []
        if hidden_count:
            output.append(
                f"[系统提示] 已隐藏 {hidden_count} 条明显垃圾或可能触发平台安全审核的搜索结果。"
                "如果剩余结果不够相关，请换关键词或改用 web_fetch/browser 访问权威来源继续验证。"
            )
        for i, r in enumerate(safe_results, 1):
            title = r.get("title", "无标题")
            url = r.get("href", r.get("link", ""))
            body = r.get("body", r.get("snippet", ""))
            engine_tag = f" [{r.get('_engine', '')}]" if r.get("_engine") else ""
            output.append(f"**{i}. {title}**{engine_tag}\n{url}\n{body}\n")

        return "\n".join(output)

    @staticmethod
    def _format_news_results(results: list) -> str:
        if not results:
            return "未找到相关新闻"

        safe_results, hidden_count = _filter_search_results(results)
        if not safe_results:
            return (
                f"新闻搜索返回了 {len(results)} 条结果，但结果内容质量不可靠或可能触发平台安全审核，"
                "已隐藏。请换用更具体关键词、web_fetch、浏览器或权威来源继续获取证据；"
                "如果当前确实没有可验证信息，请明确说明无法联网验证，不要编造结果。"
            )

        output = []
        if hidden_count:
            output.append(
                f"[系统提示] 已隐藏 {hidden_count} 条明显垃圾或可能触发平台安全审核的新闻搜索结果。"
            )
        for i, r in enumerate(safe_results, 1):
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


def create_handler(agent: Any = None):
    handler = WebSearchHandler(agent)
    return handler.handle
