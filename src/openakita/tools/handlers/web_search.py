"""
Web Search 处理器

搜索引擎优先级：Bing（中国可访问）> DuckDuckGo（兜底）
使用 httpx 直接抓取 Bing / DDGS lib 访问 DuckDuckGo。
"""

import asyncio
import logging
import re
import traceback
from typing import Any

from ...config import settings

logger = logging.getLogger(__name__)

# ── Bing HTML 搜索 ────────────────────────────────────────
# 中国网络环境下 Bing (cn.bing.com) 可直接访问，DDG 经常超时
# Bing 作为主搜索引擎，DDG 作为最低优先级兜底

_BING_SEARCH_URL = "https://cn.bing.com/search"
_BING_NEWS_URL = "https://cn.bing.com/news/search"
_BING_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_BING_ALGO_RE = re.compile(
    r'<li\s+class="b_algo"[^>]*>(.*?)</li>',
    re.DOTALL,
)
_BING_TITLE_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.+?)</a>\s*</h2>', re.DOTALL)
_BING_SNIPPET_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL)
_BING_CAPTION_RE = re.compile(r'class="b_caption"[^>]*>(.*?)</div>', re.DOTALL)
_BING_STRIP_RE = re.compile(r"<[^>]+>")
_BING_ENTITY_RE = re.compile(r"&[a-z]+;")
_BING_WS_RE = re.compile(r"\s+")

# ── 安全过滤 ──────────────────────────────────────────────


# ── 安全过滤 ──────────────────────────────────────────────

_UNSAFE_SEARCH_KEYWORDS = (
    "色情",
    "情色",
    "裸聊",
    "裸露",
    "约炮",
    "女优",
    "网黄",
    "无码视频",
    "无码",
    "强奸",
    "自慰",
    "阴茎",
    "阳具",
    "必撸",
    "porn",
    "xxx",
    "xvideo",
    "onlyfans",
)
_UNSAFE_DOMAIN_RE = re.compile(
    r"(?:^|\.)("
    r"porn|xvideos|xnxx|xhamster|onlyfans|jav|sex|adult|noduown"
    r")\.",
    re.IGNORECASE,
)


def _strip_html(text: str) -> str:
    """去除 HTML 标签和实体，压缩空白。"""
    text = _BING_STRIP_RE.sub(" ", text)
    text = _BING_ENTITY_RE.sub(" ", text)
    return _BING_WS_RE.sub(" ", text).strip()


def _fetch_bing_html(url: str, params: dict) -> str | None:
    """同步获取 Bing 搜索结果 HTML。"""
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


def _parse_bing_web_results(html: str, max_results: int) -> list[dict[str, Any]]:
    """从 Bing 网页搜索 HTML 中提取结果。"""
    results: list[dict[str, Any]] = []
    blocks = _BING_ALGO_RE.findall(html)
    if not blocks:
        # 备用：匹配其他可能的 Bing 页面结构
        alt_re = re.compile(
            r'<li[^>]*class="b_algo[^"]*"[^>]*>(.+?)</li>', re.DOTALL
        )
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

        results.append({
            "title": title,
            "href": url,
            "body": snippet,
        })

    return results


def _sync_bing_web_search(
    query: str,
    max_results: int,
) -> list[dict[str, Any]]:
    """同步执行 Bing 网页搜索（在独立线程中调用）。"""
    html = _fetch_bing_html(_BING_SEARCH_URL, {"q": query, "count": max_results})
    if not html:
        return []
    return _parse_bing_web_results(html, max_results)


def _sync_bing_news_search(
    query: str,
    max_results: int,
) -> list[dict[str, Any]]:
    """同步执行 Bing 新闻搜索（在独立线程中调用）。"""
    html = _fetch_bing_html(_BING_NEWS_URL, {"q": query, "count": max_results})
    if not html:
        return []
    # Bing 新闻搜索结果结构与普通搜索类似
    results = _parse_bing_web_results(html, max_results)
    for r in results:
        if "date" not in r:
            r["date"] = ""
        if "source" not in r:
            r["source"] = ""
    return results


# ── DDG 搜索（兜底）───────────────────────────────────────


# ── DDG 搜索（兜底）───────────────────────────────────────


def _sync_ddg_web_search(
    query: str,
    max_results: int,
    region: str,
    safesearch: str,
) -> list[dict[str, Any]]:
    """在独立线程中执行同步的 ddgs 搜索（兜底方案）"""
    from ddgs import DDGS

    with DDGS() as ddgs:
        return ddgs.text(
            query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
        )


def _sync_ddg_news_search(
    query: str,
    max_results: int,
    region: str,
    safesearch: str,
    timelimit: str | None,
) -> list[dict[str, Any]]:
    """在独立线程中执行同步的 ddgs 新闻搜索（兜底方案）"""
    from ddgs import DDGS

    with DDGS() as ddgs:
        return ddgs.news(
            query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
            timelimit=timelimit,
        )


def _result_text(result: dict[str, Any]) -> str:
    return " ".join(
        str(result.get(key, "") or "")
        for key in ("title", "href", "link", "url", "body", "snippet", "excerpt", "source")
    )


def _is_unsafe_search_result(result: dict[str, Any]) -> bool:
    """Return True only for obviously unsafe/spammy snippets.

    Keep this intentionally narrow: the goal is to prevent polluted search output
    from tripping upstream content filters, not to decide what users may search.
    """
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
    """Return the per-attempt wait budget for a search source.

    This is intentionally a soft wait budget, not a task-level failure policy:
    if the upstream search source is slow, the tool returns guidance so the
    model can continue with other sources or partial evidence.
    """
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


class WebSearchHandler:
    """Web Search 处理器"""

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
        """搜索网页 — 优先 Bing，兜底 DuckDuckGo"""
        query = params.get("query", "")
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, params.get("max_results", 5)), 20)
        region = params.get("region", "wt-wt")
        safesearch = params.get("safesearch", "moderate")
        timeout_seconds = _resolve_attempt_timeout(params)

        # ① 优先尝试 Bing（中国网络可直接访问）
        try:
            results = await _run_search_attempt(
                _sync_bing_web_search,
                timeout_seconds=timeout_seconds,
                query=query,
                max_results=max_results,
            )
            if results:
                logger.info("Bing web search returned %d results for: %s", len(results), query)
                return self._format_web_results(results)
        except TimeoutError:
            logger.info("Bing web search timed out, falling back to DDG")
        except Exception as e:
            logger.warning("Bing web search failed (%s), falling back to DDG", type(e).__name__)

        # ② 兜底：DuckDuckGo
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
            logger.warning("DDG web search timed out after %ss: %s", timeout_seconds, query)
            return _format_search_timeout("web", timeout_seconds)
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"All web search engines failed: {type(e).__name__}: {e}\n{tb}")
            return (
                "搜索暂时不可用（Bing 和 DuckDuckGo 均无法访问）。"
                "请直接告知用户\"当前无法联网搜索\"，建议稍后重试或改用其他工具，"
                "不要反复重试，也不要伪造搜索结果。"
            )

    async def _news_search(self, params: dict[str, Any]) -> str:
        """搜索新闻 — 优先 Bing，兜底 DuckDuckGo"""
        query = params.get("query", "")
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, params.get("max_results", 5)), 20)
        region = params.get("region", "wt-wt")
        safesearch = params.get("safesearch", "moderate")
        timelimit = params.get("timelimit")
        timeout_seconds = _resolve_attempt_timeout(params)

        # ① 优先尝试 Bing（中国网络可直接访问）
        try:
            results = await _run_search_attempt(
                _sync_bing_news_search,
                timeout_seconds=timeout_seconds,
                query=query,
                max_results=max_results,
            )
            if results:
                logger.info("Bing news search returned %d results for: %s", len(results), query)
                return self._format_news_results(results)
        except TimeoutError:
            logger.info("Bing news search timed out, falling back to DDG")
        except Exception as e:
            logger.warning("Bing news search failed (%s), falling back to DDG", type(e).__name__)

        # ② 兜底：DuckDuckGo
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
            logger.warning("DDG news search timed out after %ss: %s", timeout_seconds, query)
            return _format_search_timeout("news", timeout_seconds)
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"All news search engines failed: {type(e).__name__}: {e}\n{tb}")
            return (
                "新闻搜索暂时不可用（Bing 和 DuckDuckGo 均无法访问）。"
                "请直接告知用户\"当前无法联网搜索\"，建议稍后重试或改用其他工具，"
                "不要反复重试，也不要伪造搜索结果。"
            )

    @staticmethod
    def _format_web_results(results: list) -> str:
        """格式化网页搜索结果"""
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
            output.append(f"**{i}. {title}**\n{url}\n{body}\n")

        return "\n".join(output)

    @staticmethod
    def _format_news_results(results: list) -> str:
        """格式化新闻搜索结果"""
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
                "如果剩余结果不够相关，请换关键词或改用 web_fetch/browser 访问权威来源继续验证。"
            )
        for i, r in enumerate(safe_results, 1):
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


def create_handler(agent: Any = None):
    """创建 WebSearchHandler 实例并返回 handle 方法"""
    handler = WebSearchHandler(agent)
    return handler.handle
