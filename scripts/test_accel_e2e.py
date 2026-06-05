"""
OpenAkita 通用交互加速 — LM Studio 端到端测试

用法:
  python -X utf8 scripts/test_accel_e2e.py

前置条件:
  - LM Studio 运行在 http://localhost:1234/v1
  - 模型: qwen3.5-2b (意图分析), qwopus3.5-4b-coder-mtp (主模型)

测试内容:
  1. 网络连通性 + 模型列表
  2. web_search 实际搜索 (2 次: cache miss + cache hit)
  3. LLM 推理速度 (主模型 + 编译器模型)
  4. CircuitBreaker 在生产条件下的行为
  5. tool_accel 配置对实际工具的生效验证
"""

import asyncio
import hashlib
import json
import time
import urllib.request
from pathlib import Path

# ── 确保项目路径 ──
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openakita.core.tool_accelerator import (
    CircuitBreaker,
    CircuitState,
    make_cache_key,
    run_with_retry,
)
from openakita.config import settings

LMSTUDIO_URL = "http://localhost:1234/v1"

# ── 颜色输出 ──
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def p_pass(msg: str):
    print(f"  {GREEN}[PASS]{RESET} {msg}")


def p_fail(msg: str):
    print(f"  {RED}[FAIL]{RESET} {msg}")


def p_info(msg: str):
    print(f"  {CYAN}[INFO]{RESET} {msg}")


def p_section(title: str):
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")


def p_result(label: str, value: str, ok: bool = True):
    color = GREEN if ok else RED
    print(f"  {label}: {color}{value}{RESET}")


# ── 1. 网络连通性 ──
def test_connectivity():
    p_section("1. LM Studio 连通性")

    try:
        resp = urllib.request.urlopen(f"{LMSTUDIO_URL}/models", timeout=5)
        data = json.loads(resp.read())
        models = [m["id"] for m in data.get("data", [])]
        p_pass(f"LM Studio 可达, 模型数={len(models)}")
        for m in models:
            p_info(f"  模型: {m}")
        return True, models
    except Exception as e:
        p_fail(f"LM Studio 不可达: {e}")
        return False, []


# ── 2. LLM 推理速度 ──
async def test_llm_inference(model: str, task: str = "inference"):
    """测试指定模型的推理速度"""
    import httpx

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{LMSTUDIO_URL}/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "user", "content": "用一句话介绍广州"}
                    ],
                    "max_tokens": 50,
                    "temperature": 0.7,
                },
            )
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            elapsed = time.perf_counter() - t0
            tokens = usage.get("completion_tokens", len(content))
            tok_s = tokens / elapsed if elapsed > 0 else 0
            p_pass(
                f"{task} ({model}): {elapsed:.2f}s, {tokens} tokens, {tok_s:.1f} tok/s"
            )
            return elapsed, tok_s, content[:80]
    except Exception as e:
        elapsed = time.perf_counter() - t0
        p_fail(f"{task}: {e} ({elapsed:.1f}s)")
        return elapsed, 0, ""


# ── 3. web_search 实际测试 ──
async def test_web_search_e2e():
    """测试 web_search 端到端：cache miss → cache hit"""
    p_section("3. web_search 端到端测试")

    from openakita.tools.handlers.web_search import (
        WebSearchHandler,
        _search_cache,
        _SEARCH_CACHE_TTL,
        _search_cache_key,
    )

    handler = WebSearchHandler()

    # 测试查询
    query = "广州海珠区美食推荐"
    params = {"query": query, "max_results": 3, "timeout_seconds": 15}

    # ── 第1次: cache miss ──
    cache_key = _search_cache_key(query, 3, "web")
    _search_cache.pop(cache_key, None)  # 清缓存
    p_info(f"第1次搜索 (cache miss): {query}")

    t0 = time.perf_counter()
    result = await handler.handle("web_search", params)
    elapsed = time.perf_counter() - t0

    has_results = "http" in result and len(result) > 100
    if has_results:
        p_pass(f"cache MISS: {elapsed:.2f}s, 搜索结果有效")
        # 显示前200字符
        preview = result[:200].replace("\n", " ")
        p_info(f"  内容预览: {preview}...")
    else:
        p_fail(f"cache MISS: {elapsed:.2f}s, 搜索结果为空或无效")
        p_info(f"  原始输出前200字符: {result[:200]}")

    # ── 第2次: cache hit ──
    p_info(f"第2次搜索 (cache hit): {query}")
    t0 = time.perf_counter()
    result2 = await handler.handle("web_search", params)
    elapsed2 = time.perf_counter() - t0

    if elapsed2 < 1.0:
        p_pass(f"cache HIT: {elapsed2:.4f}s (<1s, 缓存生效)")
    else:
        p_fail(f"cache HIT: {elapsed2:.2f}s (应 <1s)")

    return elapsed, elapsed2


# ── 4. CircuitBreaker 生产条件 ──
async def test_circuit_breaker_production():
    """测试熔断器在实际工具调用中的表现"""
    p_section("4. CircuitBreaker 生产条件测试")

    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)

    # 用真实 HTTP 请求测试 (访问不存在的端口 → 必定失败)
    fail_count = 0

    async def failing_call():
        import httpx

        async with httpx.AsyncClient(timeout=0.5) as client:
            await client.get("http://127.0.0.1:19999/nonexistent")
        return "ok"

    for i in range(5):
        try:
            if not cb.allow_request():
                p_info(f"  第{i+1}次: 熔断器 OPEN, 跳过执行")
                continue
            await run_with_retry(
                lambda: failing_call(),
                max_retries=0,
                timeout=0.5,
            )
            cb.record_success()
        except Exception:
            fail_count += 1
            cb.record_failure()
            state_str = {CircuitState.CLOSED: "CLOSED", CircuitState.OPEN: "OPEN", CircuitState.HALF_OPEN: "HALF_OPEN"}
            p_info(f"  第{i+1}次: 失败 → 状态={state_str[cb.state]}, 失败计数={cb.failures}")

    p_info(f"等待冷却 1.5s...")
    await asyncio.sleep(1.5)
    assert cb.allow_request(), "冷却后应允许 Half-Open"
    assert cb.state == CircuitState.HALF_OPEN
    p_pass(f"冷却后进入 Half-Open (总失败={fail_count}次)")


# ── 5. 配置一致性验证 ──
def test_config_consistency():
    """验证 tool_accel 配置与现有设置的一致性"""
    p_section("5. 配置一致性验证")

    accel = settings.tool_accel

    checks = [
        ("web_search.cache_ttl", accel["web_search"]["cache_ttl"], 300),
        ("web_search.circuit_threshold", accel["web_search"]["circuit_threshold"], 5),
        ("default.timeout", accel["default"]["timeout"], 10),
        ("default.retries", accel["default"]["retries"], 1),
    ]
    for name, actual, expected in checks:
        if actual == expected:
            p_pass(f"{name} = {actual}")
        else:
            p_fail(f"{name} = {actual} (期望 {expected})")

    # 验证 web_search 在 _READ_TOOLS_FOR_CACHE 中
    from openakita.core.tool_executor import _READ_TOOLS_FOR_CACHE

    if "web_search" in _READ_TOOLS_FOR_CACHE:
        p_pass("web_search 已加入 _READ_TOOLS_FOR_CACHE (缓存白名单)")
    else:
        p_fail("web_search 未加入 _READ_TOOLS_FOR_CACHE")


# ── 主入口 ──
async def main():
    print(f"{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  OpenAkita 通用交互加速 — LM Studio 端到端测试{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")
    t_total = time.perf_counter()

    # 1. 连通性
    ok, models = test_connectivity()
    if not ok:
        print(f"\n{RED}LM Studio 不可达，终止测试{RESET}")
        return

    # 2. LLM 推理
    p_section("2. LLM 推理速度")
    model_4b = "qwopus3.5-4b-coder-mtp"
    model_2b = "qwen3.5-2b"

    if model_4b in models:
        await test_llm_inference(model_4b, "主模型 4B")
    else:
        # 回退到配置中的模型
        await test_llm_inference("qwen/qwen3.5-9b", "主模型 9B")

    if model_2b in models:
        await test_llm_inference(model_2b, "编译器模型 2B")
    else:
        p_info("编译器模型 qwen3.5-2b 未在 LM Studio 中加载")

    # 3. web_search
    t1, t2 = await test_web_search_e2e()

    # 4. CircuitBreaker
    await test_circuit_breaker_production()

    # 5. 配置一致性
    test_config_consistency()

    # ── 总结 ──
    p_section("测试总结")
    total = time.perf_counter() - t_total
    p_result("总耗时", f"{total:.1f}s")
    p_result("web_search cache MISS", f"{t1:.2f}s")
    p_result("web_search cache HIT", f"{t2:.4f}s")

    speedup = "N/A"
    if t2 > 0:
        speedup = f"{t1 / t2:.0f}x"
    p_result("缓存加速比", speedup)
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  测试完成{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
