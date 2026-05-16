"""
Web Search MCP 服务器
委托给 openakita.search.engines 共享模块 — 六引擎并行搜索 + DDG/国际Bing兜底。
"""
import json
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from mcp.server.fastmcp import FastMCP

from openakita.search.engines import (
    ENGINE_TIMEOUT,
    MERGE_LIMIT,
    get_web_engines,
    get_news_engines,
    engine_search_with_retry,
    merge_dedup_results,
    ddg_web_search,
    ddg_news_search,
    fallback_engines_search,
    all_failed_response,
)

logger = logging.getLogger(__name__)


def _parallel_search(query: str, max_results: int, engines) -> list[dict[str, Any]]:
    """并行搜索所有引擎（含每引擎重试），合并去重。"""
    t0 = time.perf_counter()

    with ThreadPoolExecutor(max_workers=len(engines)) as executor:
        futures = {
            executor.submit(engine_search_with_retry, eng, query, max_results): eng
            for eng in engines
        }
        engine_results: list[tuple[str, list[dict[str, Any]]]] = []
        for future in as_completed(futures, timeout=ENGINE_TIMEOUT + 3):
            try:
                engine_results.append(future.result())
            except (FutureTimeoutError, Exception) as exc:
                eng = futures[future]
                logger.warning(f"[{eng.label}] search timeout/error: {type(exc).__name__}")

    merged = merge_dedup_results(engine_results)
    total_ms = (time.perf_counter() - t0) * 1000
    logger.info("MCP多引擎搜索 %d 条, 耗时 %.0fms", len(merged), total_ms)
    return merged


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


# ── MCP 服务器 ────────────────────────────────────────────

mcp = FastMCP(
    name="web-search",
    instructions="""Web Search MCP Server — 六引擎并行搜索（Bing/百度/360/搜狗/神马/头条）

特性：
- 单引擎 10s 超时 + 重试 1 次（网络瞬断自动恢复）
- 合并去重取前 8 条，URL/标题合法性校验
- 全引擎失败 → DDG 兜底 → 国内友好兜底（Bing国际版）
- web_search 使用 WEB_ENGINES，news_search 使用 NEWS_ENGINES

可用工具：web_search / news_search
""",
)

_CHECKED_NETWORK = False


def _check_network() -> None:
    global _CHECKED_NETWORK
    if _CHECKED_NETWORK:
        return
    _CHECKED_NETWORK = True
    for host in ("cn.bing.com", "www.baidu.com"):
        try:
            socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
            logger.info(f"[NetworkCheck] {host} 可达")
            return
        except Exception as exc:
            logger.debug(f"[NetworkCheck] {host}: {type(exc).__name__}: {exc}")
    logger.warning("[NetworkCheck] DNS 解析外网域名失败，搜索可能无法返回结果。")


@mcp.tool()
def web_search(query: str, max_results: int = 5, region: str = "wt-wt", safesearch: str = "moderate") -> str:
    """Search the web — 6 engines parallel with retry, DDG + China-friendly fallback."""
    max_results = min(max(1, max_results), 20)
    _check_network()

    try:
        results = _parallel_search(query, max_results, get_web_engines())
        if results:
            return _format_web_results(results)
    except Exception as e:
        logger.warning(f"Multi-engine search failed, trying fallbacks: {type(e).__name__}: {e}")

    # DDG 兜底
    try:
        from ddgs import DDGS  # noqa: F401
    except ImportError:
        from openakita.tools._import_helper import import_or_hint
        return f"错误：{import_or_hint('ddgs')}"

    try:
        ddg_r = ddg_web_search(query, max_results, region, safesearch)
        if ddg_r:
            for r in ddg_r:
                r["_engine"] = "ddg"
            return _format_web_results(ddg_r[:MERGE_LIMIT])
    except Exception as e:
        logger.error(f"DDG web search failed: {type(e).__name__}: {e}")

    # P0-1: 国内友好兜底
    try:
        fb_r = fallback_engines_search(query, max_results)
        if fb_r:
            logger.info("China fallback: %d results", len(fb_r))
            return _format_web_results(fb_r[:MERGE_LIMIT])
    except Exception as e:
        logger.error(f"China fallback failed: {type(e).__name__}: {e}")

    return all_failed_response("web")


@mcp.tool()
def news_search(query: str, max_results: int = 5, region: str = "wt-wt", safesearch: str = "moderate", timelimit: str | None = None) -> str:
    """Search news — uses NEWS_ENGINES with DDG + China-friendly fallback."""
    max_results = min(max(1, max_results), 20)
    _check_network()

    # P0-2: 使用新闻专用引擎
    try:
        results = _parallel_search(query, max_results, get_news_engines())
        if results:
            return _format_news_results(results)
    except Exception as e:
        logger.warning(f"Multi-engine news search failed, trying fallbacks: {type(e).__name__}: {e}")

    # DDG 兜底
    try:
        from ddgs import DDGS  # noqa: F401
    except ImportError:
        from openakita.tools._import_helper import import_or_hint
        return f"错误：{import_or_hint('ddgs')}"

    try:
        ddg_r = ddg_news_search(query, max_results, region, safesearch, timelimit)
        if ddg_r:
            for r in ddg_r:
                r["_engine"] = "ddg"
            return _format_news_results(ddg_r[:MERGE_LIMIT])
    except Exception as e:
        logger.error(f"DDG news search failed: {type(e).__name__}: {e}")

    # P0-1: 国内友好兜底
    try:
        fb_r = fallback_engines_search(query, max_results)
        if fb_r:
            logger.info("China fallback: %d news results", len(fb_r))
            return _format_news_results(fb_r[:MERGE_LIMIT])
    except Exception as e:
        logger.error(f"China fallback failed: {type(e).__name__}: {e}")

    return all_failed_response("news")


if __name__ == "__main__":
    mcp.run()
