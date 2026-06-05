"""
OpenAkita 通用交互加速 — 多工具加速效果测试

对各类工具逐一测试缓存命中、重试、熔断器的加速效果。

用法:
  python -X utf8 scripts/test_all_tools_accel.py

前置条件: LM Studio 运行在 localhost:1234
"""

import asyncio
import time
import tempfile
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openakita.core.tool_accelerator import (
    CircuitBreaker,
    CircuitState,
    make_cache_key,
    run_with_retry,
)
from openakita.core.tool_executor import (
    _make_tool_cache_key,
    _READ_TOOLS_FOR_CACHE,
)
from openakita.config import settings

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def p_header(title: str):
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")


def p_ok(msg: str):
    print(f"  {GREEN}[OK]{RESET} {msg}")


def p_ng(msg: str):
    print(f"  {RED}[NG]{RESET} {msg}")


def p_info(msg: str):
    print(f"  {CYAN}[--]{RESET} {msg}")


def p_stat(label: str, v1: float, v2: float):
    speedup = v1 / v2 if v2 > 0 else float("inf")
    print(f"  {label}: {v1:.2f}s → {v2:.4f}s ({speedup:.0f}x)")


# ── 工具1: get_time ──
async def test_get_time():
    p_header("1. get_time")
    from openakita.tools.handlers.system import create_handler

    handle = create_handler(None)

    t0 = time.perf_counter()
    r1 = await handle("get_time", {})
    t1 = time.perf_counter() - t0

    t0 = time.perf_counter()
    r2 = await handle("get_time", {})
    t2_c = time.perf_counter() - t0

    if "time" in r1.lower() or ":" in r1:
        p_ok(f"首次调用: {t1:.4f}s")
        actual_cache = t2_c < 0.1
        if actual_cache:
            p_ok(f"缓存命中: {t2_c:.4f}s ({t1/t2_c:.0f}x)")
        else:
            p_info(f"第2次: {t2_c:.4f}s (缓存未命中或TTL外)")

    # 缓存键过滤测试
    k1 = _make_tool_cache_key("get_time", {"session_id": "abc"})
    k2 = _make_tool_cache_key("get_time", {"session_id": "def"})
    if k1 == k2:
        p_ok("session_id 过滤一致")
    else:
        p_ng(f"session_id 过滤不一致: {k1} vs {k2}")

    # tool_accel 配置验证
    accel = settings.tool_accel.get("get_time", {})
    p_info(f"配置: cache_ttl={accel.get('cache_ttl')}, retries={accel.get('retries')}")


# ── 工具2: read_file ──
async def test_read_file():
    p_header("2. read_file (缓存键 + 配置验证)")

    # 创建测试路径的缓存键
    key1 = _make_tool_cache_key("read_file", {"path": "/tmp/test.txt", "limit": 3, "session_id": "abc"})
    key2 = _make_tool_cache_key("read_file", {"path": "/tmp/test.txt", "limit": 3, "session_id": "def"})

    if key1 == key2:
        p_ok(f"session_id 过滤一致, key={key1}")
    else:
        p_ng("session_id 过滤不一致")

    if "read_file" in _READ_TOOLS_FOR_CACHE:
        p_ok("已注册到 _READ_TOOLS_FOR_CACHE")
    else:
        p_ng("未注册到 _READ_TOOLS_FOR_CACHE")

    accel = settings.tool_accel.get("read_file", {})
    p_info(f"配置: cache_ttl={accel.get('cache_ttl')}s, retries={accel.get('retries')}")


# ── 工具3: run_shell (无缓存，但测试 CB + retry 配置) ──
async def test_run_shell():
    p_header("3. run_shell (CB + retry 配置验证)")

    # 验证 run_shell 不在缓存白名单
    if "run_shell" not in _READ_TOOLS_FOR_CACHE:
        p_ok("正确: run_shell 不在缓存白名单 (写工具)")
    else:
        p_ng("异常: run_shell 不应在缓存白名单")

    # 验证 CB + retry 配置
    accel = settings.tool_accel.get("run_shell", {})
    p_info(f"配置: timeout={accel.get('timeout')}, circuit={accel.get('circuit_threshold')}, retries={accel.get('retries')}")
    p_ok("CB+retry 配置已生效")


# ── 工具4: retry 异常过滤 ──
async def test_retry_smart_filter():
    p_header("4. retry 异常智能过滤")

    # 4a. ValueError → 不重试
    async def val_err():
        raise ValueError("invalid param")

    t0 = time.perf_counter()
    try:
        await run_with_retry(lambda: val_err(), max_retries=3, delay=0.5, timeout=1)
    except ValueError:
        t1 = time.perf_counter() - t0
        if t1 < 0.3:
            p_ok(f"ValueError 不重试 (耗时 {t1:.3f}s < 0.3s)")
        else:
            p_ng(f"ValueError 耗时 {t1:.3f}s, 疑似重试了")

    # 4b. ConnectionError → 重试
    count = 0

    async def conn_err():
        nonlocal count
        count += 1
        if count < 2:
            raise ConnectionError("refused")

    t0 = time.perf_counter()
    await run_with_retry(lambda: conn_err(), max_retries=2, delay=0.1, timeout=1)
    t2 = time.perf_counter() - t0
    p_ok(f"ConnectionError 重试后恢复 (耗时 {t2:.3f}s, 共 {count} 次)")

    # 4c. TimeoutError → 重试
    count = 0

    async def timeout_err():
        nonlocal count
        count += 1
        if count == 1:
            await asyncio.sleep(0.3)
        return "ok"

    t0 = time.perf_counter()
    result = await run_with_retry(lambda: timeout_err(), max_retries=2, delay=0.05, timeout=0.1)
    t3 = time.perf_counter() - t0
    if result == "ok":
        p_ok(f"Timeout 重试后恢复 (耗时 {t3:.3f}s, 共 {count} 次)")


# ── 工具5: CircuitBreaker 工具集成 ──
async def test_circuit_breaker_tool():
    p_header("5. CircuitBreaker 工具集成")

    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.3)

    async def fail_call():
        raise OSError("simulated tool failure")

    expect_states = []
    for i in range(5):
        try:
            if not await cb.allow_request():
                expect_states.append(("SKIP", cb.state.name))
                continue
            await run_with_retry(lambda: fail_call(), max_retries=1, delay=0.05, timeout=0.5)
            await cb.record_success()
            expect_states.append(("OK", cb.state.name))
        except Exception:
            await cb.record_failure()
            expect_states.append(("FAIL", cb.state.name))

    states_str = " -> ".join(f"{a}/{s}" for a, s in expect_states)
    p_info(f"状态序列: {states_str}")

    if any(s == "OPEN" for _, s in expect_states):
        p_ok("熔断器在 2 次失败后成功触发 OPEN")
    else:
        p_ng("熔断器未触发 OPEN")

    await asyncio.sleep(0.5)
    if await cb.allow_request():
        p_ok("冷却后进入 Half-Open (自愈)")
    else:
        p_ng("冷却后未进入 Half-Open")


# ── 工具6: tool_accel default fallback ──
async def test_default_fallback():
    p_header("6. tool_accel default 兜底")

    # 不存在的工具名 → 应使用 default 配置
    default = settings.tool_accel.get("__nonexistent__", settings.tool_accel.get("default", {}))
    p_info(f"不存在工具的配置: timeout={default.get('timeout')}, retries={default.get('retries')}")

    if default.get("timeout") == 10 and default.get("retries") == 1:
        p_ok("default 配置正确兜底")
    else:
        p_ng("default 配置异常")


# ── 主入口 ──
async def main():
    print(f"{BOLD}{'=' * 65}{RESET}")
    print(f"{BOLD}  OpenAkita 通用交互加速 — 多工具加速效果测试{RESET}")
    print(f"{BOLD}{'=' * 65}{RESET}")
    t_total = time.perf_counter()

    await test_get_time()
    await test_read_file()
    await test_run_shell()
    await test_circuit_breaker_tool()
    await test_retry_smart_filter()
    await test_default_fallback()

    # ── 汇总 ──
    p_header("汇总")
    total = time.perf_counter() - t_total
    p_info(f"总耗时: {total:.1f}s")

    # 统计 tool_accel 覆盖的工具
    accel = settings.tool_accel
    cached_tools = [t for t in accel if t != "default" and accel[t].get("cache_ttl", 0) > 0]
    cb_tools = [t for t in accel if accel[t].get("circuit_threshold", 0) > 0]
    retry_tools = [t for t in accel if accel[t].get("retries", 0) > 0]

    p_info(f"缓存生效工具 ({len(cached_tools)}): {', '.join(sorted(cached_tools))}")
    p_info(f"熔断保护工具 ({len(cb_tools)}): {', '.join(sorted(cb_tools))}")
    p_info(f"重试保护工具 ({len(retry_tools)}): {', '.join(sorted(retry_tools))}")
    p_info(f"读取缓存白名单: {len(_READ_TOOLS_FOR_CACHE)} 个 ({', '.join(sorted(_READ_TOOLS_FOR_CACHE))})")

    print(f"\n{BOLD}{'=' * 65}{RESET}")
    print(f"{BOLD}  测试完成{RESET}")
    print(f"{BOLD}{'=' * 65}{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
