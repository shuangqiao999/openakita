"""
Web Search 处理器
委托给 openakita.search.engines 共享模块 — 六引擎并行搜索 + DDG/国际Bing兜底。
"""
import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from openakita.config import settings
from openakita.search.engines import (
    ENGINE_TIMEOUT,
    MERGE_LIMIT,
    get_web_engines,
    get_news_engines,
    engine_search_with_retry,
    merge_dedup_results,
    format_engine_summary,
    ddg_web_search,
    ddg_news_search,
    fallback_engines_search,
    filter_search_results,
    all_failed_response,
)

logger = logging.getLogger(__name__)


class _NoResultError(Exception):
    """标记：执行成功但无结果，用于并行降级链中区分无结果 vs 异常。"""
    pass

# ── 搜索结果缓存 ──
_search_cache: dict[str, tuple[float, str]] = {}
_SEARCH_CACHE_TTL = 300  # 5 分钟


def _search_cache_key(query: str, max_results: int, kind: str) -> str:
    raw = f"{query.strip().lower()}|{max_results}|{kind}"
    return hashlib.md5(raw.encode()).hexdigest()


def _resolve_timeout(params: dict[str, Any]) -> float:
    raw = params.get("timeout_seconds", settings.web_search_attempt_timeout_seconds)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return max(0.0, float(settings.web_search_attempt_timeout_seconds or 0))


async def _run_search_attempt(func, *, timeout_seconds: float, **kwargs) -> list[dict[str, Any]]:
    task = asyncio.to_thread(func, **kwargs)
    if timeout_seconds <= 0:
        timeout_seconds = 60.0
    return await asyncio.wait_for(task, timeout=timeout_seconds)


class WebSearchHandler:
    """多引擎并行 Web Search 处理器"""

    TOOLS = ["web_search", "news_search"]
    CHECK_HOSTS = ["cn.bing.com", "www.baidu.com"]

    def __init__(self, agent: Any = None):
        self.agent = agent

    @staticmethod
    def check_network() -> tuple[bool, str]:
        import socket as _sock
        for host in WebSearchHandler.CHECK_HOSTS:
            try:
                addr = _sock.getaddrinfo(host, 443, _sock.AF_INET, _sock.SOCK_STREAM)
                if addr:
                    return True, f"{host} 可达"
            except Exception as exc:
                logger.debug(f"[NetworkCheck] {host}: {type(exc).__name__}: {exc}")
        return False, "DNS 解析失败，请检查网络连接。"

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "web_search":
            kind = "web"
        elif tool_name == "news_search":
            kind = "news"
        else:
            return f"Unknown web search tool: {tool_name}"

        # 结果缓存检查
        cache_key = _search_cache_key(params.get("query", ""), params.get("max_results", 5), kind)
        now = time.monotonic()
        cached = _search_cache.get(cache_key)
        if cached and now - cached[0] < _SEARCH_CACHE_TTL:
            logger.info("[WebSearch] Cache HIT for query=%s", (params.get("query") or "")[:60])
            return cached[1]

        result = await self._do_search(params, kind=kind)
        _search_cache[cache_key] = (now, result)
        return result

    async def _do_search(self, params: dict[str, Any], *, kind: str) -> str:
        query = (params.get("query") or "").strip()
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, params.get("max_results", 5)), 20)
        region = params.get("region", "wt-wt")
        safesearch = params.get("safesearch", "moderate")
        timelimit = params.get("timelimit")
        overall_timeout = _resolve_timeout(params)

        # 网络预检
        if not getattr(WebSearchHandler, "_net_checked", False):
            WebSearchHandler._net_checked = True
            ok, msg = WebSearchHandler.check_network()
            if not ok:
                logger.warning(f"[WebSearch] Network pre-check failed: {msg}")
            else:
                logger.info(f"[WebSearch] Network pre-check OK: {msg}")

        search_t0 = time.perf_counter()

        # P0-2: 按 kind 选择引擎列表
        engines = get_news_engines() if kind == "news" else get_web_engines()

        # ── 并行三层：引擎并行 + DDG 兜底 + Bing 国际兜底同时发起 ──
        async def _tier1_engines():
            tasks = [
                _run_search_attempt(
                    engine_search_with_retry,
                    timeout_seconds=overall_timeout,
                    engine=eng, query=query, max_results=max_results,
                )
                for eng in engines
            ]
            all_results = await asyncio.gather(*tasks, return_exceptions=True)
            engine_results: list[tuple[str, list[dict[str, Any]]]] = []
            for eng, result in zip(engines, all_results, strict=True):
                if isinstance(result, Exception):
                    logger.warning("[%s] engine error: %s: %s", eng.label, type(result).__name__, result)
                    continue
                if result:
                    engine_results.append((eng.name, result))
            merged = merge_dedup_results(engine_results)
            if merged:
                summary = format_engine_summary(merged, engines)
                total_ms = (time.perf_counter() - search_t0) * 1000
                logger.warning(
                    "[TIMING] web_search tier1_engines total=%.0fms query=%s results=%d %s",
                    total_ms, query[:60], len(merged), summary,
                )
                return merged  # non-empty list
            logger.info("引擎并行无结果 (%d/%d), 等待兜底层", sum(1 for _ in engine_results), len(engines))
            raise _NoResultError("engines returned empty")

        async def _tier2_ddg():
            try:
                from ddgs import DDGS  # noqa: F401
            except ImportError:
                return None
            ddg_func = ddg_web_search if kind == "web" else ddg_news_search
            ddg_kwargs = {"query": query, "max_results": max_results, "region": region, "safesearch": safesearch}
            if kind == "news":
                ddg_kwargs["timelimit"] = timelimit
            ddg_results = await _run_search_attempt(ddg_func, timeout_seconds=overall_timeout, **ddg_kwargs)
            if ddg_results:
                for r in ddg_results:
                    r["_engine"] = "ddg"
                total_ms = (time.perf_counter() - search_t0) * 1000
                logger.warning(
                    "[TIMING] web_search tier2_ddg total=%.0fms query=%s results=%d",
                    total_ms, query[:60], len(ddg_results),
                )
                return ddg_results[:MERGE_LIMIT]
            return None

        async def _tier3_bing():
            fb_results = await _run_search_attempt(
                fallback_engines_search, timeout_seconds=overall_timeout,
                query=query, max_results=max_results,
            )
            if fb_results:
                total_ms = (time.perf_counter() - search_t0) * 1000
                logger.warning(
                    "[TIMING] web_search tier3_bing total=%.0fms query=%s results=%d",
                    total_ms, query[:60], len(fb_results),
                )
                return fb_results[:MERGE_LIMIT]
            return None

        tier_tasks = [
            asyncio.create_task(_tier1_engines()),
            asyncio.create_task(_tier2_ddg()),
            asyncio.create_task(_tier3_bing()),
        ]
        done, pending = await asyncio.wait(tier_tasks, return_when=asyncio.FIRST_COMPLETED,
                                            timeout=overall_timeout if overall_timeout > 0 else 60.0)
        for t in pending:
            t.cancel()
        for t in done:
            try:
                result = t.result()
                if result and (isinstance(result, list) and len(result) > 0):
                    return self._format_results(result, kind)
            except (_NoResultError, Exception):
                continue

        # 全部失败
        total_ms = (time.perf_counter() - search_t0) * 1000
        logger.warning("[TIMING] web_search ALL_FAILED total=%.0fms query=%s", total_ms, query[:60])
        return all_failed_response(kind)

    # ── 格式化 ──

    def _format_results(self, results: list, kind: str) -> str:
        if not results:
            return "未找到相关结果" if kind == "web" else "未找到相关新闻"

        safe_results, hidden_count = filter_search_results(results)
        if not safe_results:
            return (
                f"搜索返回了 {len(results)} 条结果，但内容质量不可靠或可能触发平台安全审核，"
                "已隐藏。请换用更具体关键词、web_fetch、浏览器或权威来源继续获取证据。"
            )

        if kind == "news":
            return self._format_news_items(safe_results, hidden_count)
        return self._format_web_items(safe_results, hidden_count)

    @staticmethod
    def _format_web_items(results: list, hidden_count: int) -> str:
        output = []
        if hidden_count:
            output.append(f"[系统提示] 已隐藏 {hidden_count} 条不适宜的搜索结果。")
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            url = r.get("href", r.get("link", ""))
            body = r.get("body", r.get("snippet", ""))
            engine_tag = f" [{r.get('_engine', '')}]" if r.get("_engine") else ""
            output.append(f"**{i}. {title}**{engine_tag}\n{url}\n{body}\n")
        return "\n".join(output)

    @staticmethod
    def _format_news_items(results: list, hidden_count: int) -> str:
        output = []
        if hidden_count:
            output.append(f"[系统提示] 已隐藏 {hidden_count} 条不适宜的新闻搜索结果。")
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


def create_handler(agent: Any = None):
    handler = WebSearchHandler(agent)
    return handler.handle
