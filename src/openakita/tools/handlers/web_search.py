"""
Web Search 处理器

六引擎并行搜索：Bing / Baidu / 360 / Sogou / Shenma / Toutiao
- 每个引擎独立 10s 超时
- 临时网络故障自动重试 1 次（间隔 0.5s）
- 全部引擎失败 → DDG 兜底 → 仍空则返回明确错误
- URL/标题合法性校验，解析失败不影响其他引擎
- 结构化日志：引擎耗时、成功/失败/重试
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ...config import settings

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────

_ENGINE_TIMEOUT = 10.0       # 单引擎 HTTP 请求超时（秒）
_RETRY_DELAY = 0.5            # 重试间隔（秒）
_MAX_RETRIES = 1              # 最大重试次数
_MERGE_LIMIT = 8              # 合并去重后取前 N 条

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
    """校验 URL 格式合法性。"""
    if not url:
        return False
    if not _RE_VALID_URL.match(url):
        return False
    try:
        parsed = urlparse(url)
        return bool(parsed.scheme and parsed.netloc)
    except Exception:
        return False


def _is_valid_result(r: dict[str, Any]) -> bool:
    """校验单条搜索结果的合法性。"""
    title = (r.get("title") or "").strip()
    url = (r.get("href") or r.get("link") or "").strip()
    return bool(title) and _is_valid_url(url)


def _fetch_html(url: str, params: dict, *, headers: dict | None = None, timeout: float = _ENGINE_TIMEOUT) -> str | None:
    """同步 HTTP GET，含超时控制。"""
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


# ── 搜索引擎注册 ──────────────────────────────────────────

@dataclass
class SearchEngine:
    name: str
    label: str
    search_url: str
    search_params_fn: Callable[[str, int], dict]
    parse_fn: Callable[[str, int], list[dict[str, Any]]]
    extra_headers: dict | None = None
    ua_override: str | None = None


def _bs4_parse(
    html: str,
    row_selector: str,
    title_selector: str,
    snippet_selectors: list[str],
    *,
    max_results: int = 10,
    url_attr: str = "href",
    url_formatter: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    """BeautifulSoup-based generic search result parser."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    try:
        __import__("lxml")
        parser = "lxml"
    except ImportError:
        parser = "html.parser"

    soup = BeautifulSoup(html, parser)
    results: list[dict[str, Any]] = []
    rows = soup.select(row_selector)

    for row in rows[:max_results]:
        title_el = row.select_one(title_selector)
        if not title_el:
            continue
        url = title_el.get(url_attr, "")
        if url_formatter:
            url = url_formatter(url)
        title = title_el.get_text(strip=True)
        if not title or len(title) < 2:
            continue
        snippet = ""
        for sel in snippet_selectors:
            snip_el = row.select_one(sel)
            if snip_el:
                snippet = snip_el.get_text(" ", strip=True)[:300]
                break
        if title:
            results.append({"title": title, "href": url, "body": snippet})
    return results


# ── Bing ─────────────────────────────────────────────────

def _parse_bing(html: str, max_results: int) -> list[dict[str, Any]]:
    return _bs4_parse(
        html,
        row_selector="li.b_algo",
        title_selector="h2 a",
        snippet_selectors=["p", ".b_caption p", ".b_snippet"],
        max_results=max_results,
    )


ENGINE_BING = SearchEngine(
    name="bing", label="Bing",
    search_url="https://cn.bing.com/search",
    search_params_fn=lambda q, n: {"q": q, "count": n},
    parse_fn=_parse_bing,
)

# ── 百度 ─────────────────────────────────────────────────

def _parse_baidu(html: str, max_results: int) -> list[dict[str, Any]]:
    return _bs4_parse(
        html,
        row_selector="div.c-container, div.result, div#content_left > div.result",
        title_selector="h3 a",
        snippet_selectors=[".c-abstract", ".c-span-last p", ".content-right_8Zs40"],
        max_results=max_results,
        url_formatter=_extract_url_from_baidu_redirect,
    )


ENGINE_BAIDU = SearchEngine(
    name="baidu", label="百度",
    search_url="https://www.baidu.com/s",
    search_params_fn=lambda q, n: {"wd": q, "rn": str(n)},
    parse_fn=_parse_baidu,
    extra_headers={"Referer": "https://www.baidu.com/"},
)

# ── 360 ──────────────────────────────────────────────────

def _parse_360(html: str, max_results: int) -> list[dict[str, Any]]:
    return _bs4_parse(
        html,
        row_selector="li.res-list",
        title_selector="h3 a",
        snippet_selectors=[".res-desc", "p"],
        max_results=max_results,
    )


ENGINE_360 = SearchEngine(
    name="360", label="360搜索",
    search_url="https://www.so.com/s",
    search_params_fn=lambda q, n: {"q": q},
    parse_fn=_parse_360,
)

# ── 搜狗 ─────────────────────────────────────────────────

def _parse_sogou(html: str, max_results: int) -> list[dict[str, Any]]:
    return _bs4_parse(
        html,
        row_selector="div.rb, div.vrwrap, div.vr-title",
        title_selector="h3 a, .vr-title a",
        snippet_selectors=[".str_info", ".space", ".ft", "p"],
        max_results=max_results,
    )


ENGINE_SOGOU = SearchEngine(
    name="sogou", label="搜狗",
    search_url="https://www.sogou.com/web",
    search_params_fn=lambda q, n: {"query": q},
    parse_fn=_parse_sogou,
)

# ── 神马 ─────────────────────────────────────────────────

def _parse_shenma(html: str, max_results: int) -> list[dict[str, Any]]:
    results = _bs4_parse(
        html,
        row_selector="div.card-wrap, div.card",
        title_selector="a.title, a[class*='title']",
        snippet_selectors=[".abstract", ".summary", ".desc", ".info", "p"],
        max_results=max_results,
    )
    if not results:
        results = _bs4_parse(
            html,
            row_selector="div.card-wrap, div.card, a.title",
            title_selector="a",
            snippet_selectors=["p", "span"],
            max_results=max_results,
        )
    return results


ENGINE_SHENMA = SearchEngine(
    name="shenma", label="神马",
    search_url="https://m.sm.cn/s",
    search_params_fn=lambda q, n: {"q": q},
    parse_fn=_parse_shenma,
    ua_override=_UA_MOBILE,
)

# ── 头条 ─────────────────────────────────────────────────

def _parse_toutiao(html: str, max_results: int) -> list[dict[str, Any]]:
    return _bs4_parse(
        html,
        row_selector="div.result-item, div.result, li.result, .search-result-item",
        title_selector="a[class*='title'], a",
        snippet_selectors=[".abstract", ".desc", ".snippet", ".content", "p"],
        max_results=max_results,
    )


ENGINE_TOUTIAO = SearchEngine(
    name="toutiao", label="头条",
    search_url="https://so.toutiao.com/search",
    search_params_fn=lambda q, n: {"keyword": q},
    parse_fn=_parse_toutiao,
)

_SEARCH_ENGINES: list[SearchEngine] = [
    ENGINE_BING, ENGINE_BAIDU, ENGINE_360,
    ENGINE_SOGOU, ENGINE_SHENMA, ENGINE_TOUTIAO,
]


# ── 单引擎搜索 + 重试 ─────────────────────────────────────

def _sync_engine_search_once(
    engine: SearchEngine, query: str, max_results: int,
) -> list[dict[str, Any]]:
    """单次同步搜索引擎请求（在独立线程中执行）。"""
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
    # 校验每条结果的合法性
    return [r for r in results if _is_valid_result(r)]


def _sync_engine_search_with_retry(
    engine: SearchEngine, query: str, max_results: int,
) -> list[dict[str, Any]]:
    """同步搜索引擎查询，含重试 1 次（网络错误/5xx 时）。"""
    t0 = time.perf_counter()
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            results = _sync_engine_search_once(engine, query, max_results)
            elapsed = (time.perf_counter() - t0) * 1000
            if results:
                logger.info(
                    "[%s] %d results in %.0fms (attempt %d/2) query=%s",
                    engine.label, len(results), elapsed, attempt + 1, query[:60],
                )
            else:
                logger.debug(
                    "[%s] empty in %.0fms (attempt %d/2) query=%s",
                    engine.label, elapsed, attempt + 1, query[:60],
                )
            return results
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                logger.info(
                    "[%s] attempt %d failed (%s), retrying in %.1fs...",
                    engine.label, attempt + 1, type(exc).__name__, _RETRY_DELAY,
                )
                time.sleep(_RETRY_DELAY)

    elapsed = (time.perf_counter() - t0) * 1000
    logger.warning(
        "[%s] all %d attempts failed (%.0fms): %s: %s",
        engine.label, _MAX_RETRIES + 1, elapsed, type(last_exc).__name__, last_exc,
    )
    return []


# ── 合并去重 ──────────────────────────────────────────────

def _merge_dedup_results(
    engine_results: list[tuple[str, list[dict[str, Any]]]],
    max_total: int = _MERGE_LIMIT,
) -> list[dict[str, Any]]:
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
    from collections import Counter
    hits = Counter(r.get("_engine", "?") for r in merged)
    parts = []
    for eng in _SEARCH_ENGINES:
        if eng.name in hits:
            parts.append(f"{eng.label}({hits[eng.name]}条)")
    return "、".join(parts) if parts else "0条"


# ── DDG 兜底 ──────────────────────────────────────────────

def _sync_ddg_web_search(
    query: str, max_results: int, region: str, safesearch: str,
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

_UNSAFE_KEYWORDS = (
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
    return any(keyword in text for keyword in _UNSAFE_KEYWORDS)


def _filter_search_results(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    filtered = [r for r in results if not _is_unsafe_search_result(r)]
    return filtered, len(results) - len(filtered)


def _resolve_timeout(params: dict[str, Any]) -> float:
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


# ── 统一全失败响应 ───────────────────────────────────────

def _all_failed_response(kind: str) -> str:
    label = "新闻" if kind == "news" else "网页"
    return json.dumps({
        "success": False,
        "message": (
            f"所有{label}搜索引擎（Bing/百度/360/搜狗/神马/头条 + DuckDuckGo）"
            f"均无法获取结果。请检查网络连接或稍后再试。"
        ),
        "results": [],
    }, ensure_ascii=False)


# ── Handler 类 ────────────────────────────────────────────

class WebSearchHandler:
    """多引擎并行 Web Search 处理器 — 六引擎并行 + 重试 + DDG 兜底"""

    TOOLS = ["web_search", "news_search"]

    def __init__(self, agent: Any = None):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "web_search":
            return await self._do_search(params, kind="web")
        elif tool_name == "news_search":
            return await self._do_search(params, kind="news")
        return f"Unknown web search tool: {tool_name}"

    async def _web_search(self, params: dict[str, Any]) -> str:
        return await self._do_search(params, kind="web")

    async def _news_search(self, params: dict[str, Any]) -> str:
        return await self._do_search(params, kind="news")

    async def _do_search(self, params: dict[str, Any], *, kind: str) -> str:
        query = (params.get("query") or "").strip()
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, params.get("max_results", 5)), 20)
        region = params.get("region", "wt-wt")
        safesearch = params.get("safesearch", "moderate")
        timelimit = params.get("timelimit")
        overall_timeout = _resolve_timeout(params)

        search_t0 = time.perf_counter()

        # ① 六引擎并行搜索（每个引擎独立超时 10s + 重试 1次）
        tasks = [
            _run_search_attempt(
                _sync_engine_search_with_retry,
                timeout_seconds=overall_timeout,
                engine=eng,
                query=query,
                max_results=max_results,
            )
            for eng in _SEARCH_ENGINES
        ]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        engine_results: list[tuple[str, list[dict[str, Any]]]] = []
        success_count = 0
        for eng, result in zip(_SEARCH_ENGINES, all_results, strict=True):
            if isinstance(result, Exception):
                logger.warning(
                    "[%s] engine-level error: %s: %s",
                    eng.label, type(result).__name__, result,
                )
                continue
            if result:
                success_count += 1
                engine_results.append((eng.name, result))

        # 合并去重
        merged = _merge_dedup_results(engine_results)
        total_ms = (time.perf_counter() - search_t0) * 1000

        if merged:
            summary = _format_engine_summary(merged)
            logger.info(
                "搜索「%s」→ %d条 (%s) 耗时 %.0fms %d/%d引擎成功",
                query[:60], len(merged), summary, total_ms,
                success_count, len(_SEARCH_ENGINES),
            )
            if kind == "news":
                return self._format_news_results(merged)
            return self._format_web_results(merged)

        # ② DDG 兜底
        logger.info("所有国内引擎无结果 (%d/%d成功), 尝试 DDG 兜底: %s", success_count, len(_SEARCH_ENGINES), query[:60])
        try:
            from ddgs import DDGS  # noqa: F401
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            return f"错误：{import_or_hint('ddgs')}"

        try:
            ddg_func = _sync_ddg_web_search if kind == "web" else _sync_ddg_news_search
            ddg_kwargs: dict = {"query": query, "max_results": max_results, "region": region, "safesearch": safesearch}
            if kind == "news":
                ddg_kwargs["timelimit"] = timelimit

            ddg_results = await _run_search_attempt(
                ddg_func,
                timeout_seconds=overall_timeout,
                **ddg_kwargs,
            )
            if ddg_results:
                logger.info("DDG 兜底成功: %d results for %s", len(ddg_results), query[:60])
                for r in ddg_results:
                    r["_engine"] = "ddg"
                if kind == "news":
                    return self._format_news_results(ddg_results[:8])
                return self._format_web_results(ddg_results[:8])
        except TimeoutError:
            logger.warning("DDG 兜底超时: %s", query[:60])
        except Exception as e:
            logger.error("DDG 兜底失败: %s: %s", type(e).__name__, e)

        # ③ 所有引擎 + DDG 均无结果
        logger.error("所有搜索源（6引擎+DDG）均无结果: %s", query[:60])
        return _all_failed_response(kind)

    # ── 格式化输出 ──────────────────────────────────────

    @staticmethod
    def _format_web_results(results: list) -> str:
        if not results:
            return "未找到相关结果"

        safe_results, hidden_count = _filter_search_results(results)
        if not safe_results:
            return (
                f"搜索返回了 {len(results)} 条结果，但内容质量不可靠或可能触发平台安全审核，"
                "已隐藏。请换用更具体关键词、web_fetch、浏览器或权威来源继续获取证据；"
                "如果当前确实没有可验证信息，请明确说明无法联网验证，不要编造结果。"
            )

        output = []
        if hidden_count:
            output.append(
                f"[系统提示] 已隐藏 {hidden_count} 条不适宜的搜索结果。"
                "如果剩余结果不够相关，请换关键词或改用 web_fetch/browser 访问权威来源。"
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
                f"新闻搜索返回了 {len(results)} 条结果，但内容质量不可靠或可能触发平台安全审核，"
                "已隐藏。请换用更具体关键词、web_fetch、浏览器或权威来源继续获取证据。"
            )

        output = []
        if hidden_count:
            output.append(
                f"[系统提示] 已隐藏 {hidden_count} 条不适宜的新闻搜索结果。"
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
