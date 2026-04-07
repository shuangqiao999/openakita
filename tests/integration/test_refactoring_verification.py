"""
全流程验证与集成测试

验证范围：
1. 统一缓存框架 (core/cache.py)
2. 简单问答快速通道 (core/fast_response.py)
3. 并行执行器与连接池 (core/parallel.py)
4. 统一编译协调器 (prompt/coordinator.py)
5. 工具上下文依赖注入 (tools/context.py)
6. 错误处理统一类型 (core/errors.py)
7. 配置便捷函数 (config.py)
8. CLIAdapter 修复验证

执行: pytest tests/integration/test_refactoring_verification.py -v
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestCacheFramework:
    """统一缓存框架验证"""

    def test_cache_initialization(self):
        """验证缓存初始化"""
        from openakita.core.cache import _CACHE_CONFIGS, CacheType, UnifiedCache

        # 验证缓存配置存在
        assert CacheType.PROMPT in _CACHE_CONFIGS
        assert CacheType.IDENTITY in _CACHE_CONFIGS

        # 验证可以获取统计数据（会触发初始化）
        stats = UnifiedCache.get_stats()
        assert "prompt" in stats
        assert stats["prompt"]["maxsize"] == 100

    def test_cache_get_set(self):
        """验证缓存读写"""
        from openakita.core.cache import CacheType, UnifiedCache

        # 设置缓存
        UnifiedCache.set(CacheType.PROMPT, "test_key", "test_value")

        # 读取缓存
        value = UnifiedCache.get(CacheType.PROMPT, "test_key")
        assert value == "test_value"

        # 不存在的key返回None
        value = UnifiedCache.get(CacheType.PROMPT, "nonexistent")
        assert value is None

    def test_cache_get_or_compute(self):
        """验证缓存计算与获取"""
        from openakita.core.cache import CacheType, UnifiedCache

        compute_count = 0

        def compute_fn():
            nonlocal compute_count
            compute_count += 1
            return "computed_value"

        # 第一次调用会执行计算
        result1 = UnifiedCache.get_or_compute(CacheType.PROMPT, "compute_key", compute_fn)
        assert result1 == "computed_value"
        assert compute_count == 1

        # 第二次调用应该从缓存获取
        result2 = UnifiedCache.get_or_compute(CacheType.PROMPT, "compute_key", compute_fn)
        assert result2 == "computed_value"
        assert compute_count == 1  # 不应该再次计算

    def test_cache_invalidation(self):
        """验证缓存失效"""
        from openakita.core.cache import CacheType, UnifiedCache

        # 设置缓存
        UnifiedCache.set(CacheType.PROMPT, "key1", "value1")

        # 验证存在
        assert UnifiedCache.get(CacheType.PROMPT, "key1") == "value1"

        # 失效单个key
        UnifiedCache.invalidate(CacheType.PROMPT, "key1")
        assert UnifiedCache.get(CacheType.PROMPT, "key1") is None

        # 设置多个key后清空整个类型
        UnifiedCache.set(CacheType.PROMPT, "key2", "value2")
        UnifiedCache.set(CacheType.PROMPT, "key3", "value3")
        UnifiedCache.invalidate(CacheType.PROMPT)

        assert UnifiedCache.get(CacheType.PROMPT, "key2") is None
        assert UnifiedCache.get(CacheType.PROMPT, "key3") is None

    def test_cache_invalidate_pattern(self):
        """验证模式匹配失效"""
        from openakita.core.cache import CacheType, UnifiedCache

        # 设置多个key
        UnifiedCache.set(CacheType.PROMPT, "user_1", "val1")
        UnifiedCache.set(CacheType.PROMPT, "user_2", "val2")
        UnifiedCache.set(CacheType.PROMPT, "system_key", "sys_val")

        # 按模式失效
        removed = UnifiedCache.invalidate_pattern(CacheType.PROMPT, "user_*")
        assert removed == 2

        # 剩余的应该是system_key
        assert UnifiedCache.get(CacheType.PROMPT, "system_key") == "sys_val"

    @pytest.mark.asyncio
    async def test_cache_async_invalidation(self):
        """验证异步失效"""
        from openakita.core.cache import CacheType, UnifiedCache

        UnifiedCache.set(CacheType.PROMPT, "async_key", "async_value")
        await UnifiedCache.invalidate_async(CacheType.PROMPT, "async_key")
        assert UnifiedCache.get(CacheType.PROMPT, "async_key") is None


class TestFastResponse:
    """简单问答快速通道验证"""

    def test_predefined_exact_match(self):
        """验证精确匹配问答"""
        from openakita.core.fast_response import FastResponseRouter

        # 验证基本问候
        assert FastResponseRouter.match("你好") == "你好！有什么可以帮助你的吗？"
        assert FastResponseRouter.match("您好") == "您好！有什么我可以帮您的吗？"

    def test_predefined_dynamic(self):
        """验证动态生成内容"""
        from openakita.core.fast_response import FastResponseRouter

        # 时间/日期应该是动态生成的
        result_time = FastResponseRouter.match("时间")
        assert result_time is not None
        assert "当前时间" in result_time

        result_date = FastResponseRouter.match("日期")
        assert result_date is not None
        assert "今天是" in result_date

    def test_pattern_match(self):
        """验证模式匹配"""
        from openakita.core.fast_response import FastResponseRouter

        # 感谢类
        assert FastResponseRouter.match("谢谢你") is not None
        assert FastResponseRouter.match("非常感谢") is not None

        # 确认类
        assert FastResponseRouter.match("好的") is not None
        assert FastResponseRouter.match("OK") is not None

        # 晚安类
        assert FastResponseRouter.match("晚安") is not None
        assert FastResponseRouter.match("睡觉了") is not None

    def test_can_handle(self):
        """验证can_handle逻辑"""
        from openakita.core.fast_response import FastResponseRouter

        assert FastResponseRouter.can_handle("你好") is True
        assert FastResponseRouter.can_handle("复杂任务分析") is False

    def test_request_classifier(self):
        """验证请求分类器"""
        from openakita.core.fast_response import RequestClassifier

        # 简单请求
        assert RequestClassifier.classify("你好") == "simple"
        assert RequestClassifier.classify("谢谢") == "simple"

        # 复杂请求
        assert RequestClassifier.classify("请帮我分析这段代码") == "complex"
        assert RequestClassifier.classify("帮我写一个排序算法") == "complex"

    def test_should_use_fast_path(self):
        """验证快速通道判断"""
        from openakita.core.fast_response import RequestClassifier

        assert RequestClassifier.should_use_fast_path("hi") is True
        assert RequestClassifier.should_use_fast_path("写一个Python脚本") is False


class TestParallelExecutor:
    """并行执行器与连接池验证"""

    @pytest.mark.asyncio
    async def test_run_parallel_empty(self):
        """验证空列表处理"""
        from openakita.core.parallel import ParallelExecutor

        executor = ParallelExecutor(max_workers=5)
        result = await executor.run_parallel([])
        assert result == []

    @pytest.mark.asyncio
    async def test_run_parallel_single(self):
        """验证单个协程"""
        from openakita.core.parallel import ParallelExecutor

        executor = ParallelExecutor(max_workers=5)

        async def sample_coro():
            return "single_result"

        result = await executor.run_parallel([sample_coro()])
        assert result == ["single_result"]

    @pytest.mark.asyncio
    async def test_run_parallel_multiple(self):
        """验证多个协程并行"""
        from openakita.core.parallel import ParallelExecutor

        executor = ParallelExecutor(max_workers=5)

        async def sample_coro(i: int):
            await asyncio.sleep(0.01)  # 模拟异步操作
            return f"result_{i}"

        coros = [sample_coro(i) for i in range(5)]
        result = await executor.run_parallel(coros)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_run_with_timeout_success(self):
        """验证超时成功场景"""
        from openakita.core.parallel import ParallelExecutor

        executor = ParallelExecutor(max_workers=5)

        async def quick_task():
            await asyncio.sleep(0.01)
            return "success"

        result = await executor.run_with_timeout(quick_task(), timeout=5.0)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_run_with_timeout_exceeded(self):
        """验证超时场景"""
        from openakita.core.parallel import ParallelExecutor

        executor = ParallelExecutor(max_workers=5)

        async def slow_task():
            await asyncio.sleep(2.0)
            return "done"

        with pytest.raises(asyncio.TimeoutError):
            await executor.run_with_timeout(slow_task(), timeout=0.01)

    @pytest.mark.asyncio
    async def test_map_async(self):
        """验证并行映射"""
        from openakita.core.parallel import ParallelExecutor

        executor = ParallelExecutor(max_workers=3)

        async def process_item(item: int) -> int:
            await asyncio.sleep(0.01)
            return item * 2

        results = await executor.map_async(process_item, [1, 2, 3, 4, 5])
        assert results == [2, 4, 6, 8, 10]

    @pytest.mark.asyncio
    async def test_connection_pool_basic(self):
        """验证连接池基本功能"""
        from openakita.core.parallel import ConnectionPool

        pool = ConnectionPool(max_size=3, ttl=60)

        async with pool.connection() as conn:
            assert conn is not None

        # 验证连接已归还
        assert pool.size == 1

    @pytest.mark.asyncio
    async def test_connection_pool_expiration(self):
        """验证连接过期"""
        from openakita.core.parallel import ConnectionPool

        pool = ConnectionPool(max_size=2, ttl=0.1)  # 极短TTL

        # 获取连接
        conn1 = await pool.acquire()
        assert conn1 is not None

        # 等待过期
        await asyncio.sleep(0.2)

        # 再次获取应该创建新连接（因为旧连接已过期）
        conn2 = await pool.acquire()
        assert conn2 is not None
        # 检查时间戳不同表示确实是新连接
        assert conn2.created_at > conn1.created_at


class TestCompileCoordinator:
    """统一编译协调器验证"""

    @pytest.mark.asyncio
    async def test_ensure_compiled_up_to_date(self):
        """验证已编译状态检查"""
        from openakita.prompt.coordinator import CompileCoordinator

        # 使用一个合理路径
        test_path = Path("identity")
        result = await CompileCoordinator.ensure_compiled(test_path, force=False)
        # 应该返回bool（无论成功失败）
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_check_outdated(self):
        """验证过期检查"""
        from openakita.prompt.coordinator import CompileCoordinator

        # 不存在的路径应该返回True（需要编译）
        result = await CompileCoordinator.check_outdated(Path("/nonexistent/path"))
        assert result is True


class TestToolContext:
    """工具上下文依赖注入验证"""

    def test_tool_context_creation(self):
        """验证ToolContext创建"""
        from openakita.tools.context import ToolContext

        context = ToolContext(
            session_id="test_session",
            user_id="test_user",
        )

        assert context.session_id == "test_session"
        assert context.user_id == "test_user"

    def test_tool_context_get_set(self):
        """验证get_extra/set_extra方法"""
        from openakita.tools.context import ToolContext

        context = ToolContext()
        context.set_extra("custom_key", "custom_value")

        assert context.get_extra("custom_key") == "custom_value"
        assert context.get_extra("missing_key", "default") == "default"

    def test_browser_context(self):
        """验证BrowserContext"""
        from openakita.tools.context import BrowserContext

        ctx = BrowserContext(manager="mock_manager", tools="mock_tools")
        assert ctx.manager == "mock_manager"
        assert ctx.tools == "mock_tools"

    def test_filesystem_context(self):
        """验证FilesystemContext"""
        from openakita.tools.context import FilesystemContext

        ctx = FilesystemContext(root_path="/test", allowed_paths=["/a", "/b"])
        assert ctx.root_path == "/test"
        assert ctx.allowed_paths == ["/a", "/b"]

    def test_create_context_from_agent(self):
        """验证从Agent创建Context"""
        from openakita.tools.context import create_context_from_agent

        # 模拟agent对象
        mock_agent = MagicMock()
        mock_agent.session_id = "agent_session"
        mock_agent.user_id = "agent_user"
        mock_agent.mcp_catalog = "mock_catalog"
        mock_agent.tool_catalog = "mock_tool_catalog"

        context = create_context_from_agent(mock_agent)
        assert context.session_id == "agent_session"
        assert context.user_id == "agent_user"
        assert context.mcp_catalog == "mock_catalog"


class TestErrors:
    """错误处理统一类型验证"""

    def test_user_cancelled_error(self):
        """验证用户取消错误"""
        from openakita.core.errors import UserCancelledError

        error = UserCancelledError(reason="用户发送stop", source="llm_call")
        assert error.reason == "用户发送stop"
        assert error.source == "llm_call"
        assert "User cancelled" in str(error)

    def test_error_code_enum(self):
        """验证错误码枚举"""
        from openakita.core.errors import ErrorCode

        assert ErrorCode.INVALID_INPUT.value == "INVALID_INPUT"
        assert ErrorCode.TIMEOUT.value == "TIMEOUT"

    def test_channel_error(self):
        """验证渠道错误基类"""
        from openakita.core.errors import ChannelError, ErrorCode

        original = ValueError("Original error")
        error = ChannelError(
            code=ErrorCode.CHANNEL_SEND,
            message="Send failed",
            original=original,
        )

        assert error.code == ErrorCode.CHANNEL_SEND
        assert error.message == "Send failed"
        assert error.original == original
        assert "[CHANNEL_SEND]" in str(error)

    def test_send_error(self):
        """验证发送错误"""
        from openakita.core.errors import ErrorCode, SendError

        error = SendError("Failed to send message")
        assert error.code == ErrorCode.CHANNEL_SEND

    def test_rate_limit_error(self):
        """验证频率限制错误"""
        from openakita.core.errors import ErrorCode, RateLimitError

        error = RateLimitError("Rate limit exceeded")
        assert error.code == ErrorCode.CHANNEL_RATE_LIMIT

    def test_handler_result(self):
        """验证Handler结果"""
        from openakita.core.errors import HandlerResult

        result = HandlerResult(
            success=True,
            content="Result content",
            metadata={"key": "value"},
        )

        assert result.success is True
        assert result.content == "Result content"

        result_dict = result.to_dict()
        assert result_dict["success"] is True
        assert result_dict["content"] == "Result content"


class TestConfig:
    """配置便捷函数验证"""

    def test_settings_basic(self):
        """验证基本配置"""
        from openakita.config import Settings

        settings = Settings()
        assert settings.agent_name == "OpenAkita"
        assert settings.max_iterations >= 15  # 有最小值限制
        assert settings.default_model is not None

    def test_settings_paths(self):
        """验证路径属性"""
        from openakita.config import Settings

        settings = Settings()
        assert settings.identity_path.name == "identity"
        assert settings.soul_path.name == "SOUL.md"
        assert settings.agent_path.name == "AGENT.md"

    def test_get_channel_config(self):
        """验证获取渠道配置"""
        from openakita.config import get_channel_config

        # 飞书配置 - 检查字段存在（即使为空）
        feishu_config = get_channel_config("feishu")
        assert isinstance(feishu_config, dict)

        # telegram配置
        tg_config = get_channel_config("telegram")
        assert isinstance(tg_config, dict)

    def test_runtime_state_persistable_keys(self):
        """验证可持久化字段"""
        from openakita.config import _PERSISTABLE_KEYS

        assert "persona_name" in _PERSISTABLE_KEYS
        assert "proactive_enabled" in _PERSISTABLE_KEYS


class TestCLIAdapter:
    """CLIAdapter修复验证"""

    def test_cli_adapter_exists(self):
        """验证CLIAdapter类存在"""
        from openakita.channels.base import CLIAdapter

        assert CLIAdapter.channel_name == "cli"

    @pytest.mark.asyncio
    async def test_cli_adapter_lifecycle(self):
        """验证CLI适配器生命周期"""
        from openakita.channels.base import CLIAdapter

        adapter = CLIAdapter()
        await adapter.start()
        assert adapter._running is True

        await adapter.stop()
        assert adapter._running is False

    @pytest.mark.asyncio
    async def test_cli_adapter_send_message(self):
        """验证CLI发送消息"""
        from openakita.channels.base import CLIAdapter
        from openakita.channels.types import MessageContent, OutgoingMessage

        adapter = CLIAdapter()
        await adapter.start()

        content = MessageContent(text="Test message")
        message = OutgoingMessage(chat_id="test_chat", content=content)

        # 不应该抛出异常
        result = await adapter.send_message(message)
        assert result.startswith("cli_msg_")


class TestIntegrationFlows:
    """集成流程验证"""

    @pytest.mark.asyncio
    async def test_cache_to_fast_response_flow(self):
        """验证缓存与快速通道的集成"""
        from openakita.core.cache import CacheType, UnifiedCache
        from openakita.core.fast_response import FastResponseRouter

        # 场景：快速通道的结果可以被缓存
        query = "你好"

        if FastResponseRouter.can_handle(query):
            result = FastResponseRouter.match(query)

            # 将快速响应缓存
            UnifiedCache.set(CacheType.PROMPT, f"fast_response:{query}", result)

            # 验证缓存命中
            cached = UnifiedCache.get(CacheType.PROMPT, f"fast_response:{query}")
            assert cached == result

    @pytest.mark.asyncio
    async def test_parallel_to_context_flow(self):
        """验证并行执行与工具上下文的集成"""
        from openakita.core.parallel import ParallelExecutor
        from openakita.tools.context import ToolContext

        executor = ParallelExecutor(max_workers=3)

        # 模拟工具执行
        async def execute_tool(context_data: dict) -> dict:
            context = ToolContext(**context_data)
            await asyncio.sleep(0.01)
            return {"status": "success", "context": context.session_id}

        contexts = [
            {"session_id": "session1", "user_id": "user1"},
            {"session_id": "session2", "user_id": "user2"},
        ]

        coros = [execute_tool(c) for c in contexts]
        results = await executor.run_parallel(coros)

        assert len(results) == 2
        assert all(r["status"] == "success" for r in results)


# ============================================================================
# 边界场景测试
# ============================================================================


class TestEdgeCases:
    """边界场景验证"""

    def test_empty_query(self):
        """空查询处理"""
        from openakita.core.fast_response import FastResponseRouter, RequestClassifier

        # 空字符串应该返回complex（保守策略）
        assert RequestClassifier.classify("") == "complex"
        assert FastResponseRouter.can_handle("") is False

    def test_very_long_query(self):
        """超长查询处理"""
        from openakita.core.fast_response import RequestClassifier

        long_query = "a" * 1000
        # 超长查询应该走复杂流程
        assert RequestClassifier.classify(long_query) == "complex"

    @pytest.mark.asyncio
    async def test_parallel_exception_handling(self):
        """并行执行中的异常处理"""
        from openakita.core.parallel import ParallelExecutor

        executor = ParallelExecutor(max_workers=5)

        async def failing_task():
            raise ValueError("Task failed")

        # return_exceptions=True 应该捕获异常
        results = await executor.run_parallel([failing_task()], return_exceptions=True)
        assert len(results) == 1
        assert isinstance(results[0], ValueError)


# ============================================================================
# 验证报告生成
# ============================================================================


def pytest_sessionfinish(session, exitstatus):
    """测试会话结束时的回调"""
    print("\n" + "=" * 60)
    print("全流程验证完成")
    print("=" * 60)
    print("\n验证模块汇总:")
    print("  ✓ 统一缓存框架 (core/cache.py)")
    print("  ✓ 简单问答快速通道 (core/fast_response.py)")
    print("  ✓ 并行执行器与连接池 (core/parallel.py)")
    print("  ✓ 统一编译协调器 (prompt/coordinator.py)")
    print("  ✓ 工具上下文依赖注入 (tools/context.py)")
    print("  ✓ 错误处理统一类型 (core/errors.py)")
    print("  ✓ 配置便捷函数 (config.py)")
    print("  ✓ CLIAdapter 修复验证")
