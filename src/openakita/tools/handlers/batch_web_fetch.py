"""
批量网页抓取 + 书签定向抓取处理器

batch_web_fetch: 并发抓取多个 URL，复用 web_fetch 的提取+安全逻辑
fetch_bookmarked: 从 web_bookmarks.json 按 purpose 筛选后批量抓取
"""

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...config import settings
from .web_fetch import _fetch_with_redirects, WebFetchHandler, WebFetchMeta
from ...utils.url_safety import is_safe_url

logger = logging.getLogger(__name__)

# ── 书签热重载缓存 ──
_bookmark_cache: dict[str, Any] | None = None
_bookmark_mtime: float = 0.0


def _resolve_bookmarks_path() -> Path:
    raw = settings.bookmarks_path
    p = Path(raw)
    if not p.is_absolute():
        p = settings.project_root / raw
    if not p.exists():
        # 开发模式: project_root/skills/external/...
        # 生产模式(pip install): site-packages/openakita/builtin_skills/external/...
        p = Path(__file__).parents[2] / "builtin_skills" / "external" / "web-bookmarks" / "bookmarks.json"
    return p


def _load_bookmarks() -> tuple[list[dict], str | None]:
    """加载书签配置。返回 (书签列表, 错误消息或 None)。支持热重载 + 损坏回退。"""
    global _bookmark_cache, _bookmark_mtime
    path = _resolve_bookmarks_path()
    if not path.exists():
        if _bookmark_cache is not None:
            return _bookmark_cache, None
        return [], f"书签配置文件不存在: {path}"

    try:
        current_mtime = path.stat().st_mtime
    except OSError:
        if _bookmark_cache is not None:
            return _bookmark_cache, None
        return [], f"无法读取书签文件: {path}"

    if current_mtime == _bookmark_mtime and _bookmark_cache is not None:
        return _bookmark_cache, None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        bookmarks = data.get("bookmarks", [])
        _bookmark_cache = bookmarks
        _bookmark_mtime = current_mtime
        logger.info("[BatchFetch] Loaded %d bookmarks from %s", len(bookmarks), path)
        return bookmarks, None
    except (json.JSONDecodeError, OSError) as e:
        logger.error("[BatchFetch] Bookmark config corrupted: %s — using cached version", e)
        if _bookmark_cache is not None:
            return _bookmark_cache, f"书签配置损坏({e})，使用上次缓存"
        return [], f"书签配置不可用: {e}"


class BatchWebFetchHandler:
    TOOLS = ["batch_web_fetch", "fetch_bookmarked"]

    def __init__(self, agent: Any = None):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "batch_web_fetch":
            return await self._batch_fetch(params)
        elif tool_name == "fetch_bookmarked":
            return await self._fetch_bookmarked(params)
        return f"Unknown tool: {tool_name}"

    # ── batch_web_fetch ──

    async def _batch_fetch(self, params: dict[str, Any]) -> str:
        urls = params.get("urls", [])
        if not urls:
            return "错误：urls 参数不能为空"

        accel = settings.tool_accel.get("batch_web_fetch", {})
        wf_accel = settings.tool_accel.get("web_fetch", {})
        max_concurrent = accel.get("max_concurrent") or 5
        timeout = accel.get("timeout") or wf_accel.get("timeout", 30)
        max_retries = accel.get("retries") or wf_accel.get("retries") or 0
        retry_delay = accel.get("retry_delay", 0.5)
        cb_threshold = accel.get("circuit_threshold") or wf_accel.get("circuit_threshold") or 3

        sem = asyncio.Semaphore(max_concurrent)

        async def fetch_one(url: str) -> str:
            async with sem:
                return await self._fetch_single(url, timeout, max_retries, retry_delay, cb_threshold)

        results = await asyncio.gather(*[fetch_one(u) for u in urls])
        return self._format_results(urls, results)

    async def _fetch_single(
        self, url: str, timeout: float, max_retries: int, retry_delay: float, cb_threshold: int,
    ) -> dict:
        from ...core.tool_accelerator import get_circuit_breaker, run_with_retry
        t0 = time.perf_counter()
        result: dict = {"url": url, "ok": False, "title": url, "summary": "", "error": ""}

        safe, _reason = await is_safe_url(url)
        if not safe:
            result["error"] = "URL 被安全策略拒绝（可能包含内网地址或非法协议）"
            result["title"] = "安全策略拒绝"
            logger.info("[BatchFetch] Safety rejected: %s", url)
            return result

        domain = urlparse(url).hostname or url
        breaker = get_circuit_breaker(domain, threshold=cb_threshold)
        if not await breaker.allow_request():
            result["error"] = f"域名 {domain} 暂时不可用，已跳过"
            result["title"] = "熔断器保护"
            logger.warning("[BatchFetch] CircuitBreaker OPEN for %s, skipping", domain)
            return result

        try:
            import httpx

            async def _do_fetch():
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout), follow_redirects=False,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; OpenAkita/1.0)"},
                ) as client:
                    response, meta = await _fetch_with_redirects(client, url)
                    if response is None:
                        raise RuntimeError(meta.hint or f"抓取失败 ({meta.error_code})")

                    content = response.content
                    if isinstance(content, bytes):
                        content = content.decode("utf-8", errors="replace")
                    markdown = WebFetchHandler._html_to_markdown(content, meta.final_url)
                    result["ok"] = True
                    result["title"] = meta.final_url
                    result["summary"] = markdown[:2000] if markdown else content[:2000]

            await run_with_retry(
                _do_fetch, max_retries=max_retries, delay=retry_delay, timeout=timeout,
            )
            await breaker.record_success()

        except Exception as e:
            await breaker.record_failure()
            result["error"] = f"{type(e).__name__}: {e}"
            elapsed = (time.perf_counter() - t0) * 1000
            logger.warning(
                "[BatchFetch] %s failed after %.0fms: %s", url, elapsed, e,
            )

        return result

    # ── fetch_bookmarked ──

    async def _fetch_bookmarked(self, params: dict[str, Any]) -> str:
        purpose = params.get("purpose", "").strip()
        limit = min(max(1, params.get("limit", 5)), 20)

        bookmarks, err = _load_bookmarks()
        if err and not bookmarks:
            return f"错误：{err}"

        matched = [b for b in bookmarks if b.get("enabled", True) and b.get("purpose") == purpose]
        if not matched:
            purposes = sorted({b.get("purpose", "") for b in bookmarks if b})
            hint = f"可用用途: {', '.join(purposes)}" if purposes else "书签配置为空"
            return f"未找到用途为 '{purpose}' 的书签。{hint}"

        matched.sort(key=lambda b: (-b.get("priority", 0), b.get("name", "")))
        selected = matched[:limit]
        urls = [b["url"] for b in selected]

        result = await self._batch_fetch({"urls": urls})
        if err:
            result = f"[警告] {err}\n\n{result}"

        # 在结果前添加书签来源标签
        header_lines = [f"**书签抓取 (purpose={purpose}, {len(selected)}/{len(matched)} 个):**"]
        for b in selected:
            header_lines.append(f"- **{b.get('name', b['url'])}**: {b.get('description', '')[:80]}")
        header = "\n".join(header_lines) + "\n\n---\n\n"
        return header + result

    # ── 格式化 ──

    @staticmethod
    def _format_results(urls: list[str], results: list[dict]) -> str:
        lines = []
        ok_count = sum(1 for r in results if r["ok"])
        fail_count = len(results) - ok_count

        for i, r in enumerate(results):
            if r["ok"]:
                lines.append(f"**{i + 1}. {r['title']}**")
                lines.append(r["url"])
                lines.append(r["summary"][:500])
                lines.append("")
            else:
                lines.append(f"**❌ 无法访问 ({i + 1})**")
                lines.append(r["url"])
                lines.append(f"错误原因：{r['error']}")
                lines.append("")

        if ok_count == 0 and len(results) > 0:
            return "所有网址均无法访问。\n\n" + "\n".join(lines)

        return "\n".join(lines)


def create_handler(agent: Any = None):
    handler = BatchWebFetchHandler(agent)
    return handler.handle
