"""
batch_web_fetch / fetch_bookmarked 功能测试

用法: pytest tests/test_batch_fetch.py -v
前置: 需要安装 pytest, respx (pip install respx)
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.core.tool_accelerator import get_circuit_breaker
from openakita.tools.handlers.batch_web_fetch import (
    BatchWebFetchHandler,
    _load_bookmarks,
    _resolve_bookmarks_path,
)


def p_ok(msg):
    print(f"  [PASS] {msg}")


# ── 1. 全局熔断器工厂 ──

def test_circuit_breaker_factory():
    """同一域名返回同一实例"""
    assert get_circuit_breaker("example.com") is get_circuit_breaker("example.com")
    assert get_circuit_breaker("other.com") is not get_circuit_breaker("example.com")
    p_ok("全局熔断器工厂：同域名共享实例")


def test_circuit_breaker_threshold():
    """threshold 参数生效"""
    cb = get_circuit_breaker("test-threshold.com", threshold=1)
    assert cb.threshold == 1
    p_ok("熔断器阈值可配置")


# ── 2. 书签加载 + 热重载 ──

def test_load_bookmarks_valid():
    """正常加载书签文件"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({
            "bookmarks": [
                {"name": "Test", "url": "https://example.com", "purpose": "news", "priority": 5},
                {"name": "Test2", "url": "https://example2.com", "purpose": "news", "priority": 10},
            ]
        }, f)
        path = f.name

    with patch("openakita.tools.handlers.batch_web_fetch._resolve_bookmarks_path", return_value=Path(path)):
        # Clear cache first
        import openakita.tools.handlers.batch_web_fetch as bf
        bf._bookmark_cache = None
        bf._bookmark_mtime = 0.0

        bookmarks, err = _load_bookmarks()
        assert err is None
        assert len(bookmarks) == 2
        p_ok("正常加载 2 个书签")

        # 第二次加载命中缓存
        bookmarks2, _ = _load_bookmarks()
        assert bookmarks2 is bookmarks
        p_ok("热重载：mtime 未变则命中缓存")

    Path(path).unlink(missing_ok=True)


def test_load_bookmarks_corrupted():
    """JSON 损坏时回退缓存"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write("{invalid json")
        path = f.name

    with patch("openakita.tools.handlers.batch_web_fetch._resolve_bookmarks_path", return_value=Path(path)):
        import openakita.tools.handlers.batch_web_fetch as bf

        # 先设有效缓存
        bf._bookmark_cache = [{"name": "cached", "url": "https://cached.com", "purpose": "x"}]
        bf._bookmark_mtime = 0.0

        bookmarks, err = _load_bookmarks()
        assert err is not None and "损坏" in err
        assert len(bookmarks) == 1 and bookmarks[0]["name"] == "cached"
        p_ok("JSON 损坏时回退缓存")

    Path(path).unlink(missing_ok=True)


# ── 3. 安全过滤 ──

def test_safety_filter():
    """内网 URL 被拒绝"""
    import asyncio
    from openakita.utils.url_safety import is_safe_url

    async def _check():
        safe, _ = await is_safe_url("http://192.168.1.1"); assert not safe
        safe, _ = await is_safe_url("http://localhost:8080"); assert not safe
        safe, _ = await is_safe_url("http://127.0.0.1"); assert not safe
        safe, _ = await is_safe_url("https://example.com"); assert safe
    asyncio.run(_check())
    p_ok("is_safe_url: 内网/本地拒绝, 公网放行")


# ── 4. 格式化输出 ──

def test_format_results_mixed():
    """成功+失败混排"""
    results = [
        {"ok": True, "url": "https://a.com", "title": "A", "summary": "content A"},
        {"ok": False, "url": "https://b.com", "title": "B", "summary": "", "error": "timeout"},
        {"ok": True, "url": "https://c.com", "title": "C", "summary": "content C"},
    ]
    out = BatchWebFetchHandler._format_results(["a", "b", "c"], results)
    assert "**1. A**" in out
    assert "❌ 无法访问" in out
    assert "**3. C**" in out or "**2." in out
    p_ok("格式化输出：成功+失败混排")


def test_format_results_all_failed():
    """全部失败"""
    results = [
        {"ok": False, "url": "https://a.com", "error": "timeout"},
        {"ok": False, "url": "https://b.com", "error": "refused"},
    ]
    out = BatchWebFetchHandler._format_results(["a", "b"], results)
    assert "所有网址均无法访问" in out
    p_ok("全部失败时提示明确")


# ── 5. 并发数配置 ──

def test_max_concurrent_config():
    """max_concurrent 配置从 tool_accel 读取"""
    from openakita.config import settings
    accel = settings.tool_accel.get("batch_web_fetch", {})
    mc = accel.get("max_concurrent", 5)
    assert mc == 5
    p_ok(f"max_concurrent 配置默认值 = {mc}")


# ── 6. 配置继承 ──

def test_config_inheritance():
    """batch_web_fetch timeout=None → 继承 web_fetch"""
    from openakita.config import settings
    bf = settings.tool_accel.get("batch_web_fetch", {})
    wf = settings.tool_accel.get("web_fetch", {})
    timeout = bf.get("timeout") or wf.get("timeout", 10)
    assert timeout == wf.get("timeout")
    p_ok(f"配置继承：batch_web_fetch.timeout 继承自 web_fetch.timeout={timeout}")


# ── 7. 书签路径解析 ──

def test_bookmarks_path_resolved():
    """书签路径相对于 project_root 解析"""
    path = _resolve_bookmarks_path()
    assert path.is_absolute()
    assert "web_bookmarks.json" in str(path)
    p_ok(f"书签路径解析为绝对路径: {path}")
