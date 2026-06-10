"""
批量网页抓取 + 书签定向抓取处理器

batch_web_fetch: 并发抓取多个 URL，复用 web_fetch 的提取+安全逻辑
fetch_bookmarked: 内置高质量书签，按 purpose 筛选后批量抓取
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...config import settings
from ...utils.url_safety import is_safe_url
from .web_fetch import WebFetchHandler, _fetch_with_redirects

logger = logging.getLogger(__name__)

_DEFAULT_BOOKMARKS: list[dict[str, Any]] = [
    {
        "name": "GitHub Trending",
        "url": "https://github.com/trending",
        "description": "GitHub 每日热门仓库，用于发现前沿开源项目",
        "purpose": "daily_tech_news",
        "enabled": True,
        "priority": 10,
        "ttl_seconds": 3600,
    },
    {
        "name": "Hacker News",
        "url": "https://news.ycombinator.com",
        "description": "硅谷顶级科技社区，每日最重要技术讨论",
        "purpose": "daily_tech_news",
        "enabled": True,
        "priority": 8,
    },
    {
        "name": "MIT Technology Review",
        "url": "https://www.technologyreview.com/topic/artificial-intelligence",
        "description": "权威科技媒体，提供对AI趋势的深度解读",
        "purpose": "daily_tech_news",
        "enabled": True,
        "priority": 6,
    },
    {
        "name": "Hugging Face Blog",
        "url": "https://huggingface.co/blog",
        "description": "Transformer 模型和开源 AI 生态的资讯源头",
        "purpose": "ai_research",
        "enabled": True,
        "priority": 9,
    },
    {
        "name": "OpenAI Blog",
        "url": "https://openai.com/news",
        "description": "GPT系列模型及OpenAI最新技术发布的一手信息源",
        "purpose": "ai_research",
        "enabled": True,
        "priority": 9,
    },
    {
        "name": "Google AI Blog",
        "url": "https://ai.googleblog.com",
        "description": "Google 在 AI 前沿领域的研究动态和技术分享",
        "purpose": "ai_research",
        "enabled": True,
        "priority": 8,
    },
    {
        "name": "arXiv Artificial Intelligence",
        "url": "https://arxiv.org/list/cs.AI/recent",
        "description": "AI 领域最新论文预印本，追踪前沿研究",
        "purpose": "academic_papers",
        "enabled": True,
        "priority": 7,
    },
    {
        "name": "Journal of Machine Learning Research",
        "url": "https://www.jmlr.org",
        "description": "JMLR是机器学习领域的顶级开放获取期刊",
        "purpose": "academic_papers",
        "enabled": True,
        "priority": 7,
    },
    {
        "name": "ACM Digital Library",
        "url": "https://dl.acm.org",
        "description": "全球计算机科学领域最权威的文献数据库之一",
        "purpose": "academic_papers",
        "enabled": True,
        "priority": 8,
    },
    {
        "name": "Semantic Scholar",
        "url": "https://www.semanticscholar.org",
        "description": "由AI驱动的免费学术搜索引擎，索引数亿篇论文",
        "purpose": "academic_papers",
        "enabled": True,
        "priority": 7,
    },
    {
        "name": "Gitee (码云)",
        "url": "https://gitee.com",
        "description": "国内最大的代码托管平台，适合跟踪中文开源生态",
        "purpose": "development",
        "enabled": True,
        "priority": 7,
    },
    {
        "name": "开源中国",
        "url": "https://www.oschina.net",
        "description": "国内开源技术资讯与社区，本土化内容及时",
        "purpose": "development",
        "enabled": True,
        "priority": 6,
    },
    {
        "name": "Codeberg",
        "url": "https://codeberg.org",
        "description": "GitHub之外的另一种选择，一个非营利性的开源协作平台",
        "purpose": "development",
        "enabled": True,
        "priority": 5,
    },
    {
        "name": "SourceForge",
        "url": "https://sourceforge.net",
        "description": "历史悠久的开源软件托管和发布平台",
        "purpose": "development",
        "enabled": True,
        "priority": 5,
    },
    {
        "name": "美团技术团队",
        "url": "https://tech.meituan.com",
        "description": "国内顶级技术团队的实践经验分享",
        "purpose": "technical_blog",
        "enabled": True,
        "priority": 7,
    },
    {
        "name": "Microsoft Dev Blogs",
        "url": "https://devblogs.microsoft.com",
        "description": "追踪微软技术栈（如.NET, Azure）的最新发展",
        "purpose": "technical_blog",
        "enabled": True,
        "priority": 6,
    },
    {
        "name": "36Kr",
        "url": "https://36kr.com",
        "description": "国内主流科技媒体，覆盖创业、投资和新经济动态",
        "purpose": "tech_news",
        "enabled": True,
        "priority": 7,
    },
    {
        "name": "VentureBeat",
        "url": "https://venturebeat.com",
        "description": "国际权威科技商业媒体，尤其关注AI、游戏和SaaS领域",
        "purpose": "tech_news",
        "enabled": True,
        "priority": 6,
    },
    {
        "name": "Data & Society",
        "url": "https://datasociety.net",
        "description": "专注研究数据技术对社会和文化影响的智库机构",
        "purpose": "tech_analysis",
        "enabled": True,
        "priority": 5,
    },
    {
        "name": "Oxford Analytica Daily Brief",
        "url": "https://www.oxan.com",
        "description": "每日全球地缘政治和宏观经济分析",
        "purpose": "geo_analysis",
        "enabled": True,
        "priority": 5,
    },
    {
        "name": "Google Public Data Explorer",
        "url": "https://www.google.com/publicdata",
        "description": "来自世界银行、OECD等机构的高价值公开数据集",
        "purpose": "public_data",
        "enabled": True,
        "priority": 6,
    },
    {
        "name": "World Bank Open Data",
        "url": "https://data.worldbank.org",
        "description": "全球宏观发展数据的权威来源，免费开放",
        "purpose": "public_data",
        "enabled": True,
        "priority": 7,
    },
    {
        "name": "Common Crawl",
        "url": "https://commoncrawl.org",
        "description": "庞大且免费的网络爬虫数据集，是训练模型的语料宝库",
        "purpose": "public_data",
        "enabled": True,
        "priority": 6,
    },
    {
        "name": "UCI Machine Learning Repository",
        "url": "https://archive.ics.uci.edu/ml",
        "description": "机器学习领域最经典的数据集集合站",
        "purpose": "public_data",
        "enabled": True,
        "priority": 7,
    },
    {
        "name": "Hugging Face Datasets",
        "url": "https://huggingface.co/datasets",
        "description": "AI社区的核心资源库，包含海量开源数据集",
        "purpose": "public_data",
        "enabled": True,
        "priority": 8,
    },
    {
        "name": "Reuters",
        "url": "https://www.reuters.com",
        "description": "全球最大的国际通讯社之一，新闻报道客观权威",
        "purpose": "general_news",
        "enabled": True,
        "priority": 7,
    },
    {
        "name": "The Guardian",
        "url": "https://www.theguardian.com",
        "description": "英国主流大报，深度报道和评论见长",
        "purpose": "general_news",
        "enabled": True,
        "priority": 6,
    },
    {
        "name": "Stack Overflow",
        "url": "https://stackoverflow.com",
        "description": "全球最流行的程序员技术问答社区",
        "purpose": "technical_qna",
        "enabled": True,
        "priority": 9,
    },
    {
        "name": "Python官方文档",
        "url": "https://docs.python.org/3/",
        "description": "最权威的Python语言参考手册",
        "purpose": "official_doc",
        "enabled": True,
        "priority": 10,
    },
]

_bookmark_cache: list[dict] | None = None
_bookmark_mtime: float = 0.0


def _load_bookmarks() -> tuple[list[dict], str | None]:
    """加载书签。优先使用用户自定义文件，否则返回内置默认书签。"""
    global _bookmark_cache, _bookmark_mtime
    raw = settings.bookmarks_path
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = settings.project_root / raw
        if p.exists():
            try:
                current_mtime = p.stat().st_mtime
                if current_mtime == _bookmark_mtime and _bookmark_cache is not None:
                    return _bookmark_cache, None
                data = json.loads(p.read_text(encoding="utf-8"))
                bookmarks = data.get("bookmarks", [])
                _bookmark_cache = bookmarks
                _bookmark_mtime = current_mtime
                return bookmarks, None
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("[BatchFetch] Custom bookmarks file error: %s, using built-in", e)
    return _DEFAULT_BOOKMARKS, None


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
                return await self._fetch_single(
                    url, timeout, max_retries, retry_delay, cb_threshold
                )

        results = await asyncio.gather(*[fetch_one(u) for u in urls])
        return self._format_results(urls, results)

    async def _fetch_single(
        self,
        url: str,
        timeout: float,
        max_retries: int,
        retry_delay: float,
        cb_threshold: int,
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
                    timeout=httpx.Timeout(timeout),
                    follow_redirects=False,
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
                _do_fetch,
                max_retries=max_retries,
                delay=retry_delay,
                timeout=timeout,
            )
            await breaker.record_success()

        except Exception as e:
            await breaker.record_failure()
            result["error"] = f"{type(e).__name__}: {e}"
            elapsed = (time.perf_counter() - t0) * 1000
            logger.warning(
                "[BatchFetch] %s failed after %.0fms: %s",
                url,
                elapsed,
                e,
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
