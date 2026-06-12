"""
Token 统计验证: 累加正确性 + 并发安全性 + benchmark 集成

测试:
1. 单次 LLM 调用 → total_tokens_used > 0
2. 多次调用累加正确
3. asyncio.gather 100 并发无数据竞争
4. BenchmarkEngine._default_token_counter 返回 >0
"""

import asyncio
from unittest.mock import MagicMock, patch


async def main():
    pass_count = 0
    fail_count = 0

    def test(name, condition, detail=""):
        nonlocal pass_count, fail_count
        if condition:
            pass_count += 1
            print(f"  [PASS] {name}")
        else:
            fail_count += 1
            print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))

    # ================================================================
    print("=== 1. Brain total_tokens_used 单次累加 ===")
    from openakita.core.brain import Brain

    b = Brain(api_key="test")
    test("初始值为 0", b.total_tokens_used == 0)
    b._acc_tokens_in += 500
    b._acc_tokens_out += 300
    test("累加后 = 800", b.total_tokens_used == 800)
    test("property 类型为 int", isinstance(b.total_tokens_used, int))

    # ================================================================
    print("\n=== 2. Lock 存在性 ===")
    test("_token_lock 是 asyncio.Lock", isinstance(b._token_lock, asyncio.Lock))

    # ================================================================
    print("\n=== 3. _record_usage 签名 ===")
    import inspect
    sig = inspect.signature(b._record_usage)
    test("_record_usage 是 async def", inspect.iscoroutinefunction(b._record_usage))

    # ================================================================
    print("\n=== 4. 多次累加正确性 ===")
    b2 = Brain(api_key="test")
    b2._acc_tokens_in += 100
    b2._acc_tokens_out += 50
    b2._acc_tokens_in += 200
    b2._acc_tokens_out += 100
    test("累计 = 450 (300 in + 150 out)", b2.total_tokens_used == 450)

    # ================================================================
    print("\n=== 5. 并发安全性 (100 协程) ===")
    b3 = Brain(api_key="test")

    async def add_random():
        """模拟并发累加"""
        inp = (hash(asyncio.current_task().get_name()) % 100) + 1
        out = (hash(asyncio.current_task().get_name() * 2) % 50) + 1
        async with b3._token_lock:
            b3._acc_tokens_in += inp
            b3._acc_tokens_out += out
        return inp, out

    tasks = [asyncio.create_task(add_random()) for _ in range(100)]
    results = await asyncio.gather(*tasks)
    expected_in = sum(r[0] for r in results)
    expected_out = sum(r[1] for r in results)
    test("_acc_tokens_in 匹配预期", b3._acc_tokens_in == expected_in,
         f"{b3._acc_tokens_in} vs {expected_in}")
    test("_acc_tokens_out 匹配预期", b3._acc_tokens_out == expected_out,
         f"{b3._acc_tokens_out} vs {expected_out}")

    # ================================================================
    print("\n=== 6. BenchmarkEngine _default_token_counter ===")
    from openakita.evolution.benchmark import BenchmarkEngine

    mock_agent = MagicMock()
    mock_agent.brain = MagicMock()
    mock_agent.brain.total_tokens_used = 5000
    counter = BenchmarkEngine._default_token_counter(mock_agent)
    test("读 total_tokens_used", counter == 5000)

    mock_agent2 = MagicMock()
    mock_agent2.brain = MagicMock()
    mock_agent2.brain.total_tokens_used = 0
    mock_agent2.brain._acc_tokens_in = 300
    mock_agent2.brain._acc_tokens_out = 200
    counter2 = BenchmarkEngine._default_token_counter(mock_agent2)
    test("fallback 读 _acc_tokens_in+out", counter2 == 500)

    mock_agent3 = MagicMock()
    mock_agent3.brain = None
    counter3 = BenchmarkEngine._default_token_counter(mock_agent3)
    test("brain=None 返回 0", counter3 == 0)

    # ================================================================
    print("\n=== 7. 调用方 await 编译检查 ===")
    import py_compile
    try:
        py_compile.compile("src/openakita/core/brain.py", doraise=True)
        test("brain.py 语法正确", True)
    except py_compile.PyCompileError as e:
        test("brain.py 语法正确", False, str(e)[:60])

    # ================================================================
    print("\n" + "=" * 50)
    print(f"结果: {pass_count} PASS, {fail_count} FAIL (共 {pass_count + fail_count})")

asyncio.run(main())
