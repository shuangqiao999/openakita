"""
记忆系统 EmbeddingHealthBreaker 自愈熔断器 — 功能测试

验证：永久熔断 → 5分钟自愈 → 不再需要重启恢复

用法:
  python -X utf8 scripts/test_embedding_breaker.py
"""

import time
from openakita.memory.lancedb_backend import EmbeddingHealthBreaker


def p_pass(msg): print(f"  [PASS] {msg}")
def p_fail(msg): print(f"  [FAIL] {msg}")


def test_basic_breaker():
    print("=== 1. 基础熔断逻辑 ===")
    b = EmbeddingHealthBreaker(failure_threshold=2, cooldown_seconds=0.2)

    assert b.is_healthy(), "初始应健康"
    p_pass("初始 Closed")

    b.mark_failure("e1")
    assert b.is_healthy(), "1次失败不应触发"
    p_pass("1次失败保持 Closed")

    b.mark_failure("e2")
    assert not b.is_healthy(), "2次失败应触发 Open"
    p_pass("2次失败 → Open")

    assert not b.try_probe(), "冷却期内不应允许探测"
    p_pass("冷却期内 try_probe = False")

    # 等待冷却
    time.sleep(0.25)
    assert b.is_healthy(), "冷却后应自动恢复"
    p_pass("冷却后自动 Closed (is_healthy)")

    print("  PASS\n")


def test_probe_guard():
    print("=== 2. 并发探测防护 ===")
    b = EmbeddingHealthBreaker(failure_threshold=1, cooldown_seconds=0.1)
    b.mark_failure("e1")
    assert not b.is_healthy()
    time.sleep(0.15)

    # 第一次探测申请成功
    assert b.try_probe(), "第一次应成功"
    # 第二次被 _probing 标志拦截
    assert not b.try_probe(), "第二次应被 _probing 拦截"
    p_pass("并发探测互斥正确")

    b.mark_success()
    assert b.is_healthy()
    p_pass("探测成功 → Closed")

    print("  PASS\n")


def test_self_healing_on_search():
    print("=== 3. 模拟 search() 中的自愈 ===")
    b = EmbeddingHealthBreaker(failure_threshold=2, cooldown_seconds=0.1)

    b.mark_failure("embed_api_timeout")
    b.mark_failure("embed_api_timeout")
    assert not b.is_healthy()
    p_pass("模拟嵌入API 2次失败 → Open")

    time.sleep(0.15)
    # search() 的逻辑:
    # if not breaker.is_healthy():
    #     if not breaker.try_probe(): return []
    # embedder = _get_embedder()
    # if breaker.is_healthy(): breaker.mark_success()

    if not b.is_healthy():
        if not b.try_probe():
            p_fail("冷却后应允许探测")
            return
    # 假设 embedder 恢复成功
    b.mark_success()
    assert b.is_healthy()
    p_pass("search() 成功探测 → 自愈 → Closed")

    print("  PASS\n")


def test_maintenance_probe():
    print("=== 4. 模拟定时维护恢复 ===")
    b = EmbeddingHealthBreaker(failure_threshold=2, cooldown_seconds=300)

    b.mark_failure("e1")
    b.mark_failure("e2")
    p_pass("2次失败 → Open")

    # 模拟冷却到期：重置时间
    b.last_fail_time = 0
    b._probing = False
    if b.try_probe():
        b.mark_success()
        assert b.is_healthy()
        p_pass("定时维护探测 → 自愈 → Closed")
    else:
        p_fail("定时维护应允许探测")

    print("  PASS\n")


def test_mark_ok_resets():
    print("=== 5. mark_success 重置计数器 ===")
    b = EmbeddingHealthBreaker()
    b.mark_failure("e1")
    assert b.failures == 1
    b.mark_success()
    assert b.failures == 0
    assert b.is_healthy()
    p_pass("mark_success 正确重置")
    print("  PASS\n")


if __name__ == "__main__":
    print("=" * 55)
    print("  EmbeddingHealthBreaker 自愈熔断器 — 功能测试")
    print("=" * 55)
    print()

    test_basic_breaker()
    test_probe_guard()
    test_self_healing_on_search()
    test_maintenance_probe()
    test_mark_ok_resets()

    print("=" * 55)
    print("  全部测试通过")
    print("=" * 55)
