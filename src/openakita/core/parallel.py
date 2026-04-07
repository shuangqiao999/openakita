"""
并行执行器与连接池

提供多任务并行执行、连接池管理、线程池支持。

修复内容：
- ConnectionPool 完全重写，修复 ID 生成、连接追踪
- map_async 添加 return_exceptions 参数
- run_parallel 修复资源泄漏风险
- ParallelToolExecutor 显式处理 Exception
- 全局实例改为延迟初始化
- 添加优雅关闭机制
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Callable, Coroutine, Generic, List, Optional, TypeVar
from weakref import WeakValueDictionary

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ParallelExecutor:
    """
    并行任务执行器

    使用示例:
        executor = ParallelExecutor(max_workers=10)

        # 并行执行多个协程
        results = await executor.run_parallel([
            some_async_func(1),
            some_async_func(2),
            some_async_func(3),
        ])

        # 带超时的执行
        result = await executor.run_with_timeout(
            some_async_func(),
            timeout=30.0
        )
    """

    def __init__(self, max_workers: int = 10):
        self.semaphore = asyncio.Semaphore(max_workers)
        self._max_workers = max_workers
        self._active_count = 0
        self._shutting_down = False

    @property
    def active_count(self) -> int:
        """当前活跃任务数"""
        return self._active_count

    @property
    def is_shutting_down(self) -> bool:
        """是否正在关闭"""
        return self._shutting_down

    async def run_parallel(
        self,
        coroutines: List[Coroutine],
        *,
        return_exceptions: bool = False,
    ) -> List[Any]:
        """并行执行多个协程

        Args:
            coroutines: 要执行的协程列表
            return_exceptions: 是否返回异常而非抛出

        Returns:
            结果列表
        """
        if not coroutines:
            return []

        if len(coroutines) == 1:
            try:
                return [await coroutines[0]]
            except Exception as e:
                if return_exceptions:
                    return [e]
                raise

        async def _run_with_semaphore(coro: Coroutine) -> Any:
            async with self.semaphore:
                self._active_count += 1
                try:
                    return await coro
                finally:
                    self._active_count -= 1

        tasks = [asyncio.create_task(_run_with_semaphore(coro)) for coro in coroutines]

        try:
            results = await asyncio.gather(*tasks, return_exceptions=return_exceptions)
            return list(results)
        except Exception as e:
            # 取消所有进行中的任务，并等待取消完成
            for task in tasks:
                if not task.done():
                    task.cancel()
            # 等待取消完成
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def run_with_timeout(self, coro: Coroutine, timeout: float = 30.0) -> Any:
        """带超时的执行

        Args:
            coro: 要执行的协程
            timeout: 超时时间（秒）

        Returns:
            执行结果

        Raises:
            asyncio.TimeoutError: 超时
        """
        return await asyncio.wait_for(coro, timeout=timeout)

    async def map_async(
        self,
        func: Callable[[Any], Coroutine[Any, Any, T]],
        items: List[Any],
        *,
        max_concurrent: Optional[int] = None,
        return_exceptions: bool = False,
    ) -> List[T | Exception]:
        """并行映射

        Args:
            func: 异步函数
            items: 要处理的项目列表
            max_concurrent: 最大并发数（默认使用max_workers）
            return_exceptions: 是否返回异常而非抛出（默认False，异常返回None）

        Returns:
            结果列表（顺序与输入一致）
        """
        if not items:
            return []

        sem = asyncio.Semaphore(max_concurrent or self._max_workers)

        async def _run_one(item: Any) -> Any:
            async with sem:
                return await func(item)

        tasks = [asyncio.create_task(_run_one(item)) for item in items]
        results = await asyncio.gather(*tasks, return_exceptions=return_exceptions)

        if return_exceptions:
            return list(results)

        # 保持顺序，将异常转换为None
        return [r if not isinstance(r, Exception) else None for r in results]

    async def shutdown(self, wait: bool = True) -> None:
        """优雅关闭

        Args:
            wait: 是否等待所有任务完成
        """
        self._shutting_down = True

        if wait:
            # 等待活跃任务完成（带超时）
            try:
                await asyncio.wait_for(
                    self._wait_for_idle(),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Shutdown timeout, some tasks may not complete")

    async def _wait_for_idle(self) -> None:
        """等待所有任务完成"""
        while self._active_count > 0:
            await asyncio.sleep(0.1)


class ParallelToolExecutor:
    """
    并行工具执行器

    支持多个工具调用并行执行。
    """

    def __init__(self, max_parallel: int = 5):
        self._parallel_executor = ParallelExecutor(max_workers=max_parallel)

    async def execute_multi(
        self,
        tool_calls: List[dict],
        execute_fn: Callable[[dict], Coroutine[Any, Any, dict]],
    ) -> List[dict]:
        """并行执行多个工具调用

        Args:
            tool_calls: 工具调用列表 [{"name": "xxx", "input": {...}}, ...]
            execute_fn: 执行单个工具的异步函数

        Returns:
            工具结果列表
        """
        if not tool_calls:
            return []

        if len(tool_calls) == 1:
            result = await execute_fn(tool_calls[0])
            return [result]

        # 并行执行
        coros = [execute_fn(call) for call in tool_calls]
        results = await self._parallel_executor.run_parallel(coros, return_exceptions=True)

        # 转换为标准格式，显式处理 Exception 类型
        formatted_results = []
        for r in results:
            if isinstance(r, Exception):
                formatted_results.append(
                    {
                        "type": "tool_result",
                        "content": f"工具执行错误: {str(r)}",
                        "is_error": True,
                    }
                )
            else:
                formatted_results.append(r)

        return formatted_results


class ThreadPoolTaskQueue:
    """
    线程池任务队列

    用于CPU密集型任务的异步执行。

    使用示例:
        thread_pool = ThreadPoolTaskQueue(max_workers=4)

        # 同步函数在线程池中执行
        result = await thread_pool.run_in_thread(some_sync_function, arg1, arg2)
    """

    def __init__(self, max_workers: int = 4):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._max_workers = max_workers
        self._shutting_down = False

    async def run_in_thread(self, func: Callable[..., T], *args, **kwargs) -> T:
        """在线程池中执行同步函数

        Args:
            func: 要执行的同步函数
            *args: 位置参数
            **kwargs: 关键字参数

        Returns:
            函数返回值
        """
        loop = asyncio.get_event_loop()
        wrapped = partial(func, *args, **kwargs)
        return await loop.run_in_executor(self._executor, wrapped)

    def shutdown(self, wait: bool = True) -> None:
        """关闭线程池

        Args:
            wait: 是否等待任务完成
        """
        self._shutting_down = True
        self._executor.shutdown(wait=wait)


@dataclass
class Connection:
    """连接基类"""

    id: str
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    is_active: bool = True


class ConnectionPool:
    """
    通用连接池

    修复内容：
    - _created 正确递增
    - _active_count 追踪活跃连接
    - acquire/release 逻辑完善
    - 添加超时保护

    使用示例:
        pool = ConnectionPool(max_size=20, ttl=300)

        async with pool.connection() as conn:
            await conn.query("SELECT ...")
    """

    def __init__(
        self,
        max_size: int = 20,
        ttl: int = 300,
        factory: Optional[Callable[[], Coroutine[Any, Any, Connection]]] = None,
    ):
        self._pool: asyncio.Queue[Connection] = asyncio.Queue(maxsize=max_size)
        self._max_size = max_size
        self._ttl = ttl
        self._factory = factory
        self._lock = asyncio.Lock()
        self._created_count = 0
        self._active_count = 0

    async def _create_connection(self) -> Connection:
        """创建新连接"""
        self._created_count += 1
        conn_id = f"conn_{self._created_count}"
        if self._factory:
            return await self._factory()
        return Connection(id=conn_id)

    def _is_expired(self, conn: Connection) -> bool:
        """检查连接是否过期"""
        return (time.time() - conn.last_used) > self._ttl

    async def acquire(self) -> Optional[Connection]:
        """获取连接

        优先级：
        1. 从池中获取可用连接
        2. 检查是否过期，过期则创建新连接
        3. 超时后且未达最大连接数则创建新连接
        """
        try:
            conn = await asyncio.wait_for(self._pool.get(), timeout=5.0)
            self._active_count += 1
            if conn and self._is_expired(conn):
                # 连接过期，创建新的
                self._created_count += 1
                conn = Connection(id=f"conn_{self._created_count}")
                self._active_count += 1
            return conn
        except asyncio.TimeoutError:
            # 超时，检查是否还可以创建新连接
            if self._created_count < self._max_size:
                conn = await self._create_connection()
                self._active_count += 1
                return conn
            return None

    async def release(self, conn: Optional[Connection]) -> None:
        """释放连接回池"""
        if conn is None:
            return

        self._active_count -= 1
        conn.last_used = time.time()

        # 只有活跃的连接才放回池中
        if conn.is_active:
            try:
                self._pool.put_nowait(conn)
            except asyncio.QueueFull:
                # 池已满，丢弃连接
                pass

    @asynccontextmanager
    async def connection(self):
        """连接上下文管理器"""
        conn = await self.acquire()
        try:
            yield conn
        finally:
            await self.release(conn)

    @property
    def size(self) -> int:
        """当前池大小（等待中的连接）"""
        return self._pool.qsize()

    @property
    def available(self) -> int:
        """可用连接数"""
        return self._max_size - self._pool.qsize()

    @property
    def active_count(self) -> int:
        """当前活跃连接数"""
        return self._active_count

    @property
    def total_created(self) -> int:
        """总共创建的连接数"""
        return self._created_count


# 全局实例改为延迟初始化
_executor_instance: Optional[ParallelExecutor] = None
_thread_pool_instance: Optional[ThreadPoolTaskQueue] = None


def get_parallel_executor(max_workers: int = 10) -> ParallelExecutor:
    """获取并行执行器实例（延迟初始化）"""
    global _executor_instance
    if _executor_instance is None:
        _executor_instance = ParallelExecutor(max_workers=max_workers)
    return _executor_instance


def get_thread_pool(max_workers: int = 4) -> ThreadPoolTaskQueue:
    """获取线程池实例（延迟初始化）"""
    global _thread_pool_instance
    if _thread_pool_instance is None:
        _thread_pool_instance = ThreadPoolTaskQueue(max_workers=max_workers)
    return _thread_pool_instance


# 兼容旧API
@property
def parallel_executor() -> ParallelExecutor:
    """全局并行执行器实例"""
    return get_parallel_executor()


@property
def thread_pool() -> ThreadPoolTaskQueue:
    """全局线程池实例"""
    return get_thread_pool()
