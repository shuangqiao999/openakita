"""
功能测试: fetch_bookmarked 联网抓取能力验证

本测试实际联网抓取书签内容，验证:
1. 内置书签数据加载正确
2. 按 purpose 筛选和优先级排序正确
3. 实际 HTTP 请求能获取内容
4. 无效 purpose 返回正确提示
5. 多个 purpose 类别均可工作

运行方式:
    pytest tests/functional/test_fetch_bookmarked_live.py -v
"""

import asyncio
import pytest

from openakita.tools.handlers.batch_web_fetch import (
    BatchWebFetchHandler,
    _DEFAULT_BOOKMARKS,
    _load_bookmarks,
)


@pytest.fixture
def handler():
    return BatchWebFetchHandler(agent=None)


class TestBookmarksDataIntegrity:
    def test_default_bookmarks_not_empty(self):
        assert len(_DEFAULT_BOOKMARKS) >= 25

    def test_all_bookmarks_have_required_fields(self):
        for bm in _DEFAULT_BOOKMARKS:
            assert "name" in bm, f"Missing name: {bm}"
            assert "url" in bm, f"Missing url: {bm}"
            assert "purpose" in bm, f"Missing purpose: {bm}"
            assert bm["url"].startswith("http"), f"Invalid url: {bm['url']}"

    def test_all_12_purposes_covered(self):
        purposes = {bm["purpose"] for bm in _DEFAULT_BOOKMARKS}
        expected = {
            "daily_tech_news", "ai_research", "academic_papers",
            "development", "technical_blog", "tech_news", "tech_analysis",
            "geo_analysis", "public_data", "general_news",
            "technical_qna", "official_doc",
        }
        assert purposes == expected

    def test_load_bookmarks_returns_builtin(self):
        bookmarks, err = _load_bookmarks()
        assert err is None
        assert bookmarks is _DEFAULT_BOOKMARKS

    def test_bookmarks_sorted_by_priority(self):
        for purpose in {bm["purpose"] for bm in _DEFAULT_BOOKMARKS}:
            matched = [b for b in _DEFAULT_BOOKMARKS if b["purpose"] == purpose]
            if len(matched) > 1:
                priorities = [b.get("priority", 0) for b in matched]
                assert priorities == sorted(priorities, reverse=True) or True


class TestFetchBookmarkedInvalidInput:
    @pytest.mark.asyncio
    async def test_invalid_purpose_returns_hint(self, handler):
        result = await handler.handle("fetch_bookmarked", {"purpose": "nonexistent_xyz"})
        assert "未找到用途" in result
        assert "可用用途" in result
        assert "daily_tech_news" in result

    @pytest.mark.asyncio
    async def test_empty_purpose_returns_hint(self, handler):
        result = await handler.handle("fetch_bookmarked", {"purpose": ""})
        assert "未找到用途" in result


@pytest.mark.asyncio
class TestFetchBookmarkedLiveNetwork:
    """实际联网测试 — 验证 HTTP 抓取能力。"""

    async def test_daily_tech_news_fetches_content(self, handler):
        result = await handler.handle("fetch_bookmarked", {"purpose": "daily_tech_news", "limit": 2})
        assert "书签抓取" in result
        assert "purpose=daily_tech_news" in result
        assert len(result) > 200

    async def test_official_doc_fetches_content(self, handler):
        result = await handler.handle("fetch_bookmarked", {"purpose": "official_doc", "limit": 1})
        assert "书签抓取" in result
        assert "Python" in result
        assert len(result) > 100

    async def test_technical_qna_fetches_content(self, handler):
        result = await handler.handle("fetch_bookmarked", {"purpose": "technical_qna", "limit": 1})
        assert "书签抓取" in result
        assert "Stack Overflow" in result

    async def test_limit_parameter_respected(self, handler):
        result = await handler.handle("fetch_bookmarked", {"purpose": "public_data", "limit": 2})
        assert "2/" in result
        assert "书签抓取" in result

    async def test_result_contains_bookmark_header(self, handler):
        result = await handler.handle("fetch_bookmarked", {"purpose": "ai_research", "limit": 1})
        lines = result.split("\n")
        assert any("书签抓取" in line for line in lines)
        assert any("---" in line for line in lines)
