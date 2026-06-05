"""
OpenAkita 通用交互加速 — 功能测试

用法:
  python -X utf8 scripts/test_acceleration.py
"""

import asyncio
import time

from openakita.core.tool_accelerator import (
    CircuitBreaker,
    CircuitState,
    make_cache_key,
    run_with_retry,
)
from openakita.config import settings


def p_pass(msg: str):
    print(f"  [PASS] {msg}")


def p_fail(msg: str):
    print(f"  [FAIL] {msg}")


# ── 1. CircuitBreaker 三态逻辑（async） ──
async def test_circuit_breaker():
    print("=== 测试1: CircuitBreaker 三态 ===")
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.3)

    assert await cb.allow_request(), "Closed 状态应允许请求"
    assert cb.state == CircuitState.CLOSED
    p_pass("初始 Closed，允许请求")

    await cb.record_failure()
    assert await cb.allow_request()
    assert cb.state == CircuitState.CLOSED
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN
    p_pass("2 次失败 → Open")

    assert not await cb.allow_request(), "Open 状态应拒绝请求"
    p_pass("Open 状态拒绝请求")

    print("  ... 等待冷却 0.4s ...")
    await asyncio.sleep(0.4)
    assert await cb.allow_request()
    assert cb.state == CircuitState.HALF_OPEN
    p_pass("冷却后进入 Half-Open，允许试探")

    await cb.record_success()
    assert cb.state == CircuitState.CLOSED
    p_pass("试探成功 → Closed")

    # Half-Open 只允许一次试探
    await cb.record_failure()
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN
    await asyncio.sleep(0.4)
    assert await cb.allow_request()
    assert cb.state == CircuitState.HALF_OPEN
    # 第二个并发请求被拒绝（_probing=True）
    assert not await cb.allow_request(), "Half-Open 只允许一次试探"
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN
    p_pass("Half-Open 试探失败 → 重回 Open，并发保护生效")

    print("  PASS: CircuitBreaker 三态逻辑正确\n")


# ── 2. run_with_retry ──
async def test_run_with_retry():
    print("=== 测试2: run_with_retry ===")

    async def always_ok():
        return "ok"

    result = await run_with_retry(lambda: always_ok(), max_retries=2, timeout=1)
    assert result == "ok"
    p_pass("成功不重试")

    count = 0

    async def fail_twice_then_ok():
        nonlocal count
        count += 1
        if count < 3:
            raise ConnectionError(f"attempt {count}")
        return "recovered"

    result = await run_with_retry(lambda: fail_twice_then_ok(), max_retries=3, delay=0.05, timeout=1)
    assert result == "recovered" and count == 3
    p_pass(f"2次失败后第3次恢复 (共 {count} 次)")

    count = 0

    async def raise_value_error():
        nonlocal count
        count += 1
        raise ValueError("bad param")

    try:
        await run_with_retry(lambda: raise_value_error(), max_retries=3, delay=0.05, timeout=1)
        assert False, "应该抛出 ValueError"
    except ValueError:
        assert count == 1
        p_pass("ValueError 不重试 (立即失败)")

    count = 0

    async def timeout_then_ok():
        nonlocal count
        count += 1
        if count == 1:
            await asyncio.sleep(0.3)
        return "ok"

    result = await run_with_retry(lambda: timeout_then_ok(), max_retries=2, delay=0.05, timeout=0.1)
    assert result == "ok"
    p_pass(f"超时重试后恢复 (共 {count} 次)")

    print("  PASS: run_with_retry 重试+异常过滤正确\n")


# ── 3. make_cache_key ──
def test_make_cache_key():
    print("=== 测试3: make_cache_key ===")

    k1 = make_cache_key("web_search", {"query": "广州美食", "max_results": 5})
    k2 = make_cache_key("web_search", {"max_results": 5, "query": "广州美食"})
    assert k1 == k2 and k1 is not None
    p_pass("参数顺序无关，生成相同 key")

    k3 = make_cache_key("test", {"items": [1, 2, 3], "name": "test"})
    assert k3 is not None
    p_pass("嵌套 list 安全处理")

    k4 = make_cache_key("test", {"query": "x", "session_id": "abc"})
    k5 = make_cache_key("test", {"query": "x", "session_id": "def"})
    assert k4 == k5
    p_pass("忽略字段 session_id 生效")

    # 自定义对象 → 返回 None（跳过缓存）
    class Foo:
        pass

    k6 = make_cache_key("test", {"obj": Foo(), "query": "x"})
    assert k6 is None, "不可序列化对象应返回 None"
    p_pass("自定义对象安全返回 None")

    print("  PASS: make_cache_key 安全性正确\n")


# ── 4. tool_accel 配置 ──
def test_tool_accel_config():
    print("=== 测试4: tool_accel 配置 ===")
    accel = settings.tool_accel
    assert "web_search" in accel and "default" in accel
    assert accel["web_search"]["timeout"] == 25
    assert accel["default"]["timeout"] == 10
    p_pass(f"web_search timeout={accel['web_search']['timeout']}, default={accel['default']['timeout']}")
    print("  PASS: tool_accel 配置正确\n")


# ── 5. ToolExecutor 加速集成 ──
async def test_tool_executor_acceleration():
    print("=== 测试5: ToolExecutor 加速集成 ===")
    from openakita.core.tool_executor import _make_tool_cache_key, _READ_TOOLS_FOR_CACHE

    assert "web_search" in _READ_TOOLS_FOR_CACHE
    p_pass("web_search 已注册到读缓存白名单")

    # 缓存键生成（无动态字段）
    key1 = _make_tool_cache_key("web_search", {"query": "test", "max_results": 5})
    key2 = _make_tool_cache_key("web_search", {"query": "test", "max_results": 5, "session_id": "abc"})
    assert key1 == key2, "session_id 应被过滤"
    p_pass("session_id 过滤生效，缓存键一致")

    print("  PASS: ToolExecutor 加速集成就绪\n")


async def main():
    print("=" * 50)
    print("OpenAkita 通用交互加速 — 功能测试")
    print("=" * 50 + "\n")

    await test_circuit_breaker()
    await test_run_with_retry()
    test_make_cache_key()
    test_tool_accel_config()
    await test_tool_executor_acceleration()

    print("=" * 50)
    print("全部测试通过")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
