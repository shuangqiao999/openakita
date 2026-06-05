"""
OpenAkita 通用交互加速 — 本地 lmstudio 功能测试

用法:
  python scripts/test_acceleration.py

前置条件:
  - OpenAkita 后端已安装 (pip install -e .)
  - LM Studio 正在运行，端口 1234
  - 已配置 data/llm_endpoints.json，指向 http://localhost:1234/v1

测试内容:
  1. CircuitBreaker 三态逻辑
  2. run_with_retry 重试 + 异常过滤
  3. make_cache_key 安全性
  4. tool_accel 配置加载
  5. ToolExecutor 缓存命中 (模拟)

用法:
  cd E:\gongxiang\openakita
  python scripts/test_acceleration.py
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


# ── 1. CircuitBreaker 三态逻辑 ──
def test_circuit_breaker():
    """测试熔断器 Closed → Open → Half-Open → Closed 全流程"""
    print("=== 测试1: CircuitBreaker 三态 ===")
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.3)

    # 正常通过
    assert cb.allow_request(), "Closed 状态应允许请求"
    assert cb.state == CircuitState.CLOSED
    print("  ✓ 初始 Closed，允许请求")

    # 失败 → 尚未达到阈值
    cb.record_failure()
    assert cb.allow_request()
    assert cb.state == CircuitState.CLOSED
    cb.record_failure()  # 第2次失败 → 达到阈值
    assert cb.state == CircuitState.OPEN
    print("  ✓ 2 次失败 → Open")

    # Open → 拒绝请求
    assert not cb.allow_request(), "Open 状态应拒绝请求"
    print("  ✓ Open 状态拒绝请求")

    # 等待冷却 → Half-Open
    print("  ... 等待冷却 0.4s ...")
    time.sleep(0.4)
    assert cb.allow_request()
    assert cb.state == CircuitState.HALF_OPEN
    print("  ✓ 冷却后进入 Half-Open，允许试探")

    # Half-Open 试探成功 → Closed
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    print("  ✓ 试探成功 → Closed")

    # Half-Open 试探失败 → Open
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.4)
    cb.allow_request()  # 进入 Half-Open
    cb.record_failure()  # 试探失败
    assert cb.state == CircuitState.OPEN
    print("  ✓ Half-Open 试探失败 → 重回 Open")

    print("  PASS: CircuitBreaker 三态逻辑正确\n")


# ── 2. run_with_retry ──
async def test_run_with_retry():
    """测试重试 + 异常过滤"""
    print("=== 测试2: run_with_retry ===")

    # 2a. 成功无需重试
    async def always_ok():
        return "ok"

    result = await run_with_retry(lambda: always_ok(), max_retries=2, timeout=1)
    assert result == "ok"
    print("  ✓ 成功不重试")

    # 2b. 网络异常重试
    count = 0

    async def fail_twice_then_ok():
        nonlocal count
        count += 1
        if count < 3:
            raise ConnectionError(f"attempt {count}")
        return "recovered"

    result = await run_with_retry(
        lambda: fail_twice_then_ok(),
        max_retries=3,
        delay=0.05,
        timeout=1,
    )
    assert result == "recovered"
    assert count == 3
    print(f"  ✓ 2次失败后第3次恢复 (共 {count} 次)")

    # 2c. ValueError 不重试
    count = 0

    async def raise_value_error():
        nonlocal count
        count += 1
        raise ValueError("bad param")

    try:
        await run_with_retry(
            lambda: raise_value_error(),
            max_retries=3,
            delay=0.05,
            timeout=1,
        )
        assert False, "应该抛出 ValueError"
    except ValueError:
        assert count == 1
        print("  ✓ ValueError 不重试 (立即失败)")

    # 2d. 超时重试
    count = 0

    async def timeout_then_ok():
        nonlocal count
        count += 1
        if count == 1:
            await asyncio.sleep(0.3)
        return "ok"

    result = await run_with_retry(
        lambda: timeout_then_ok(),
        max_retries=2,
        delay=0.05,
        timeout=0.1,
    )
    assert result == "ok"
    print(f"  ✓ 超时重试后恢复 (共 {count} 次)")

    print("  PASS: run_with_retry 重试+异常过滤正确\n")


# ── 3. make_cache_key ──
def test_make_cache_key():
    """测试缓存键生成安全性"""
    print("=== 测试3: make_cache_key ===")

    # 正常 dict
    k1 = make_cache_key("web_search", {"query": "广州美食", "max_results": 5})
    k2 = make_cache_key("web_search", {"max_results": 5, "query": "广州美食"})
    assert k1 == k2, "参数顺序无关"
    assert k1 is not None
    print("  ✓ 参数顺序无关，生成相同 key")

    # 嵌套 list (不可哈希)
    k3 = make_cache_key("test", {"items": [1, 2, 3], "name": "test"})
    assert k3 is not None
    print("  ✓ 嵌套 list 安全处理")

    # 忽略字段
    k4 = make_cache_key("test", {"query": "x", "session_id": "abc"})
    k5 = make_cache_key("test", {"query": "x", "session_id": "def"})
    assert k4 == k5, "session_id 应被忽略"
    print("  ✓ 忽略字段 session_id 生效")

    print("  PASS: make_cache_key 安全性正确\n")


# ── 4. tool_accel 配置 ──
def test_tool_accel_config():
    """测试 tool_accel 配置正确加载"""
    print("=== 测试4: tool_accel 配置 ===")
    accel = settings.tool_accel

    assert "web_search" in accel
    assert "default" in accel
    assert accel["web_search"]["timeout"] == 25
    assert accel["web_search"]["circuit_threshold"] == 5
    assert accel["default"]["timeout"] == 10
    assert accel["default"]["retries"] == 1
    print("  ✓ web_search timeout=25, default retries=1")
    print("  ✓ 配置包含字段: " + ", ".join(sorted(accel["web_search"].keys())))
    print("  PASS: tool_accel 配置正确\n")


# ── 5. ToolExecutor 加速集成 (模拟) ──
async def test_tool_executor_acceleration():
    """测试 ToolExecutor 的缓存 + retry 集成 (不启动完整实例)"""
    print("=== 测试5: ToolExecutor 加速集成 ===")

    from openakita.core.tool_executor import (
        _make_tool_cache_key,
        _READ_TOOLS_FOR_CACHE,
    )

    # web_search 已在 _READ_TOOLS_FOR_CACHE 中
    assert "web_search" in _READ_TOOLS_FOR_CACHE
    print("  ✓ web_search 已注册到读缓存白名单")

    # 缓存键生成
    key = _make_tool_cache_key("web_search", {"query": "test", "max_results": 5})
    assert key is not None
    print(f"  ✓ 缓存键生成: {key}")

    print("  PASS: ToolExecutor 加速集成就绪\n")


# ── 主入口 ──
async def main():
    print("=" * 50)
    print("OpenAkita 通用交互加速 — 功能测试")
    print("=" * 50)
    print()

    test_circuit_breaker()
    await test_run_with_retry()
    test_make_cache_key()
    test_tool_accel_config()
    await test_tool_executor_acceleration()

    print("=" * 50)
    print("全部测试通过")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
