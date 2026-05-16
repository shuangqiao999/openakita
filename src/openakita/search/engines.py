"""
OpenAkita 搜索引擎共享模块

六引擎并行搜索：Bing / Baidu / 360 / Sogou / Shenma / Toutiao
- 每个引擎独立 10s 超时 + 重试 1 次（间隔 0.5s）
- 全部引擎失败 → DDG 兜底 → 仍空则返回明确错误
- URL/标题合法性校验，解析失败不影响其他引擎
- 结构化日志：引擎耗时、成功/失败/重试

供 tools/handlers/web_search.py 和 mcp_servers/web_search.py 共用。
"""

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────

ENGINE_TIMEOUT = 10.0
RETRY_DELAY = 0.5
MAX_RETRIES = 1
MERGE_LIMIT = 8

UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
UA_MOBILE = (
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


def is_valid_url(url: str) -> bool:
    if not url or not _RE_VALID_URL.match(url):
        return False
    try:
        parsed = urlparse(url)
        return bool(parsed.scheme and parsed.netloc)
    except Exception:
        return False


def is_valid_result(r: dict[str, Any]) -> bool:
    title = (r.get("title") or "").strip()
    url = (r.get("href") or r.get("link") or "").strip()
    return bool(title) and is_valid_url(url)


def fetch_html(
    url: str,
    params: dict,
    *,
    headers: dict | None = None,
    timeout: float = ENGINE_TIMEOUT,
) -> str | None:
    """同步 HTTP GET，使用 httpx.Timeout 精确控制连接/读取/写入超时。

    不再使用全局 socket.setdefaulttimeout()，避免影响进程中其他 socket 操作。
    """
    default_headers = {
        "User-Agent": UA_DESKTOP,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if headers:
        default_headers.update(headers)

    try:
        httpx_timeout = httpx.Timeout(timeout, connect=5.0)
        with httpx.Client(timeout=httpx_timeout, follow_redirects=True) as client:
            resp = client.get(url, params=params, headers=default_headers)
            resp.raise_for_status()
            return resp.text
    except Exception as exc:
        logger.debug(f"[HTTP] {url.split('?')[0]}: {type(exc).__name__}: {exc}")
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


# ── Bing（国际版 + 国内版）──────────────────────────────

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

# P0-1: Bing 国际版作为国内 DDG 兜底的替代
ENGINE_BING_INTL = SearchEngine(
    name="bing_intl", label="Bing(国际)",
    search_url="https://www.bing.com/search",
    search_params_fn=lambda q, n: {"q": q, "count": n},
    parse_fn=_make_standard_parser(_BING_BLOCK_RE, _BING_TITLE_RE, _BING_SNIPPET_RE),
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

# ── 百度新闻 ────────────────────────────────────────────

ENGINE_BAIDU_NEWS = SearchEngine(
    name="baidu_news", label="百度新闻",
    search_url="https://www.baidu.com/s",
    search_params_fn=lambda q, n: {"wd": q, "rn": str(n), "tn": "news"},
    parse_fn=_parse_baidu,
    extra_headers={"Referer": "https://www.baidu.com/"},
)

# ── 360 ──────────────────────────────────────────────────

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

# ── 搜狗 ─────────────────────────────────────────────────

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

# ── 搜狗新闻 ────────────────────────────────────────────

ENGINE_SOGOU_NEWS = SearchEngine(
    name="sogou_news", label="搜狗新闻",
    search_url="https://news.sogou.com/news",
    search_params_fn=lambda q, n: {"query": q},
    parse_fn=_make_standard_parser(_SOGOU_BLOCK_RE, _SOGOU_TITLE_RE, _SOGOU_SNIPPET_RE),
)

# ── 神马 ─────────────────────────────────────────────────

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
    ua_override=UA_MOBILE,
)

# ── 头条 ─────────────────────────────────────────────────

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
    ua_override=UA_MOBILE,  # P3-1: 头条主要面向移动端
)

# ── 头条新闻 ────────────────────────────────────────────

ENGINE_TOUTIAO_NEWS = SearchEngine(
    name="toutiao_news", label="头条新闻",
    search_url="https://so.toutiao.com/search",
    search_params_fn=lambda q, n: {"keyword": q},
    parse_fn=_parse_toutiao,
    ua_override=UA_MOBILE,
)

# ── 引擎列表 ─────────────────────────────────────────────

WEB_ENGINES: list[SearchEngine] = [
    ENGINE_BING, ENGINE_BAIDU, ENGINE_360,
    ENGINE_SOGOU, ENGINE_SHENMA, ENGINE_TOUTIAO,
]

# P0-2: 新闻专用引擎列表
NEWS_ENGINES: list[SearchEngine] = [
    ENGINE_BING, ENGINE_BAIDU_NEWS, ENGINE_SOGOU_NEWS, ENGINE_TOUTIAO_NEWS,
]

# P0-1: DDG 国内不可用时的国内友好兜底引擎
_FALLBACK_CHINA_ENGINES: list[SearchEngine] = [
    ENGINE_BING_INTL,  # Bing 国际版
]


def get_web_engines() -> list[SearchEngine]:
    """返回网页搜索引擎列表（可通过 settings 配置过滤）。"""
    try:
        from openakita.config import settings
        enabled = getattr(settings, "search_enabled_engines", None)
        if enabled and isinstance(enabled, (list, tuple)):
            return [e for e in WEB_ENGINES if e.name in enabled]
    except Exception:
        pass
    return list(WEB_ENGINES)


def get_news_engines() -> list[SearchEngine]:
    """返回新闻搜索引擎列表（可通过 settings 配置过滤）。"""
    try:
        from openakita.config import settings
        enabled = getattr(settings, "search_news_enabled_engines", None)
        if enabled and isinstance(enabled, (list, tuple)):
            return [e for e in NEWS_ENGINES if e.name in enabled]
    except Exception:
        pass
    return list(NEWS_ENGINES)


def get_fallback_engines() -> list[SearchEngine]:
    """国内友好的兜底引擎列表（替代 DDG）。"""
    return list(_FALLBACK_CHINA_ENGINES)


# ── 单引擎搜索 + 重试 ─────────────────────────────────────

def engine_search_once(
    engine: SearchEngine, query: str, max_results: int,
) -> list[dict[str, Any]]:
    """单次同步搜索引擎请求。"""
    params = engine.search_params_fn(query, max_results)
    headers_to_use = {}
    if engine.extra_headers:
        headers_to_use = dict(engine.extra_headers)
    if engine.ua_override:
        headers_to_use.setdefault("User-Agent", engine.ua_override)
    html = fetch_html(engine.search_url, params, headers=headers_to_use or None, timeout=ENGINE_TIMEOUT)
    if not html:
        return []
    try:
        results = engine.parse_fn(html, max_results)
    except Exception as exc:
        logger.warning(f"[{engine.label}] parse failed: {type(exc).__name__}: {exc}")
        return []
    # P2-1: 解析成功但结果为0 → WARNING（可能是HTML结构变化）
    if not results:
        logger.warning(
            "[%s] parse returned 0 results — HTML structure may have changed. query='%s'",
            engine.label, query[:60],
        )
    return [r for r in results if is_valid_result(r)]


def engine_search_with_retry(
    engine: SearchEngine, query: str, max_results: int,
) -> list[dict[str, Any]]:
    """同步搜索引擎查询，含重试（网络错误/5xx 时）。"""
    t0 = time.perf_counter()
    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            results = engine_search_once(engine, query, max_results)
            elapsed = (time.perf_counter() - t0) * 1000
            if results:
                logger.info(
                    "[%s] %d results in %.0fms (attempt %d/%d) query=%s",
                    engine.label, len(results), elapsed, attempt + 1, MAX_RETRIES + 1, query[:60],
                )
            return results
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                logger.info(
                    "[%s] attempt %d failed (%s), retrying in %.1fs...",
                    engine.label, attempt + 1, type(exc).__name__, RETRY_DELAY,
                )
                time.sleep(RETRY_DELAY)

    elapsed = (time.perf_counter() - t0) * 1000
    logger.warning(
        "[%s] all %d attempts failed (%.0fms): %s: %s",
        engine.label, MAX_RETRIES + 1, elapsed, type(last_exc).__name__, last_exc,
    )
    return []


# ── 合并去重 ──────────────────────────────────────────────

def merge_dedup_results(
    engine_results: list[tuple[str, list[dict[str, Any]]]],
    max_total: int = MERGE_LIMIT,
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


def format_engine_summary(merged: list[dict[str, Any]], engines: list[SearchEngine]) -> str:
    from collections import Counter
    hits = Counter(r.get("_engine", "?") for r in merged)
    parts = []
    for eng in engines:
        if eng.name in hits:
            parts.append(f"{eng.label}({hits[eng.name]}条)")
    return "、".join(parts) if parts else "0条"


# ── DDG 兜底（海外） + 国内友好兜底 ─────────────────────────

def ddg_web_search(
    query: str, max_results: int, region: str, safesearch: str,
) -> list[dict[str, Any]]:
    """DuckDuckGo 网页搜索 — 仅海外网络环境可用。"""
    from ddgs import DDGS
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results, region=region, safesearch=safesearch))


def ddg_news_search(
    query: str, max_results: int, region: str, safesearch: str, timelimit: str | None,
) -> list[dict[str, Any]]:
    """DuckDuckGo 新闻搜索 — 仅海外网络环境可用。"""
    from ddgs import DDGS
    with DDGS() as ddgs:
        return ddgs.news(
            query, max_results=max_results, region=region, safesearch=safesearch,
            timelimit=timelimit,
        )


def fallback_engines_search(
    query: str, max_results: int,
) -> list[dict[str, Any]]:
    """P0-1: 国内友好兜底 — 使用 Bing 国际版等替代 DDG。"""
    results: list[dict[str, Any]] = []
    for eng in get_fallback_engines():
        try:
            engine_results = engine_search_once(eng, query, max_results)
            for r in engine_results:
                r["_engine"] = eng.name
            results.extend(engine_results)
            if results:
                break
        except Exception as e:
            logger.debug(f"[Fallback] {eng.label}: {e}")
    return results


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


def filter_search_results(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    filtered = [r for r in results if not _is_unsafe_search_result(r)]
    return filtered, len(results) - len(filtered)


# ── 统一全失败响应 ───────────────────────────────────────

def all_failed_response(kind: str) -> str:
    label = "新闻" if kind == "news" else "网页"
    return json.dumps(
        {
            "success": False,
            "message": (
                f"所有{label}搜索引擎（Bing/百度/360/搜狗/神马/头条 + DDG/国际Bing）"
                f"均无法获取结果。请检查网络连接或稍后再试。"
            ),
            "results": [],
        },
        ensure_ascii=False,
    )


# ── 搜索结果相关性评分 ─────────────────────────────────────

def score_search_relevance(
    query: str, result: dict[str, Any]
) -> tuple[float, list[str]]:
    """对搜索结果进行相关性评分。

    基于查询关键词在标题/摘要中的出现情况进行打分：
    - 1.0: 所有关键实体均出现在标题中
    - 0.5: 部分匹配
    - 0.1: 无匹配或弱匹配

    Returns:
        (score, matched_entities) — 0.0~1.0 的评分和匹配到的实体列表
    """
    if not query:
        return 0.1, []
    query_lower = query.lower()
    text = (
        f"{result.get('title', '')} {result.get('body', '')} "
        f"{result.get('snippet', '')} {result.get('excerpt', '')} "
        f"{result.get('source', '')} {result.get('abstract', '')}"
    ).lower()

    words = [w.strip().strip('"\'""''，。！？、；：""''「」') for w in query_lower.split()]
    words = [w for w in words if len(w) >= 2]
    if not words:
        return 0.3, []

    title = (result.get("title", "") or "").lower()

    matched: list[str] = []
    full_match = True
    for w in words:
        if w in title or w in text:
            matched.append(w)
        else:
            full_match = False

    if not matched:
        return 0.1, []
    if full_match and all(w in title for w in words):
        return 1.0, matched
    return 0.5, matched


def filter_by_relevance(
    results: list[dict[str, Any]],
    query: str,
    min_score: float = 0.3,
) -> list[dict[str, Any]]:
    """按相关性过滤搜索结果。

    Args:
        results: 原始搜索结果列表
        query: 搜索查询
        min_score: 最低相关性评分阈值

    Returns:
        过滤后的结果列表（每个结果附加 _relevance_score 字段）
    """
    if not query or not results:
        return results
    scored: list[tuple[float, list[str], dict[str, Any]]] = []
    for r in results:
        score, matched = score_search_relevance(query, r)
        r["_relevance_score"] = score
        if score >= min_score:
            scored.append((score, matched, r))
    scored.sort(key=lambda x: -x[0])
    filtered = [r for _, _, r in scored]
    if filtered:
        logger.debug(
            "[Relevance] %d/%d results above threshold %.2f for query '%s'",
            len(filtered),
            len(results),
            min_score,
            query[:60],
        )
    return filtered


# ── 引擎健康追踪 ────────────────────────────────────────────

_engine_failure_counts: dict[str, int] = {}
_MAX_CONSECUTIVE_FAILURES = 5  # 连续失败超过此值自动禁用引擎


def record_engine_success(engine_name: str) -> None:
    """记录引擎成功调用，重置失败计数。"""
    if engine_name in _engine_failure_counts:
        if _engine_failure_counts[engine_name] > 0:
            logger.info(
                "[EngineHealth] %s recovered after %d failures",
                engine_name,
                _engine_failure_counts[engine_name],
            )
    _engine_failure_counts[engine_name] = 0


def record_engine_failure(engine_name: str) -> bool:
    """记录引擎失败。返回 True 表示引擎应被禁用。"""
    current = _engine_failure_counts.get(engine_name, 0) + 1
    _engine_failure_counts[engine_name] = current
    if current >= _MAX_CONSECUTIVE_FAILURES:
        logger.warning(
            "[EngineHealth] %s has failed %d consecutive times, disabling.",
            engine_name,
            current,
        )
        return True
    return False


def is_engine_disabled(engine_name: str) -> bool:
    """检查引擎是否因连续失败被禁用。"""
    return _engine_failure_counts.get(engine_name, 0) >= _MAX_CONSECUTIVE_FAILURES


def get_disabled_engines() -> list[str]:
    """获取当前被禁用的引擎名称列表。"""
    return [
        name
        for name, count in _engine_failure_counts.items()
        if count >= _MAX_CONSECUTIVE_FAILURES
    ]


# ── 查询扩展 ───────────────────────────────────────────────

def expand_query(query: str) -> list[str]:
    """生成同义词扩展查询列表，用于搜索无结果时的重试。

    对中英文混合查询生成变体，不改变核心语义。
    """
    variations: list[str] = [query]
    query_lower = query.lower()
    if "trump" in query_lower or "特朗普" in query:
        pass  # 专有名词不扩展
    if "visit" in query_lower or "visit" in query_lower.split():
        if "visit" in query_lower and "visits" not in query_lower:
            variations.append(query_lower.replace("visit", "visits"))
    if "china" in query_lower or "中国" in query:
        if "china" in query_lower and "chinese" not in query_lower:
            variations.append(query_lower.replace("china", "chinese"))
    if "may" in query_lower.split() and "2026" in query_lower:
        variations.append(query_lower.replace("may", "May"))
    if "may" not in query_lower.split() and "2026" in query_lower:
        pass  # 保持原始查询
    return variations[:3]
