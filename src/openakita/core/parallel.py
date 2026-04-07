"""
并行执行器与连接池

提供多任务并行执行、连接池管理、线程池支持。
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Callable, Coroutine, List, Optional
from typing import TypeVar

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

    @property
    def active_count(self) -> int:
        """当前活跃任务数"""
        return self._active_count

    async def run_parallel(
        self, coroutines: List[Coroutine], *, return_exceptions: bool = False
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
            # 取消所有进行中的任务
            for task in tasks:
                if not task.done():
                    task.cancel()
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
        func: Callable[[Any], Coroutine],
        items: List[Any],
        *,
        max_concurrent: Optional[int] = None,
    ) -> List[Any]:
        """并行映射

        Args:
            func: 异步函数
            items: 要处理的项目列表
            max_concurrent: 最大并发数（默认使用max_workers）

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
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 保持顺序
        return [r if not isinstance(r, Exception) else None for r in results]


class ParallelToolExecutor:
    """
    并行工具执行器

    支持多个工具调用并行执行。
    """

    def __init__(self, max_parallel: int = 5):
        self._parallel_executor = ParallelExecutor(max_workers=max_parallel)

    async def execute_multi(
        self, tool_calls: List[dict], execute_fn: Callable[[dict], Coroutine]
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
        results = await self._parallel_executor.run_parallel(coros)

        # 转换为标准格式
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

    使用示例:
        pool = ConnectionPool(max_size=20, ttl=300)

        async with pool.connection() as conn:
            await conn.query("SELECT ...")
    """

    def __init__(
        self, max_size: int = 20, ttl: int = 300, factory: Optional[Callable[[], Coroutine]] = None
    ):
        self._pool: asyncio.Queue[Optional[Connection]] = asyncio.Queue(maxsize=max_size)
        self._max_size = max_size
        self._ttl = ttl
        self._created = 0
        self._factory = factory  # 创建连接的工厂函数
        self._lock = asyncio.Lock()

    async def _create_connection(self) -> Optional[Connection]:
        """创建新连接"""
        if self._factory:
            return await self._factory()
        return Connection(id=f"conn_{self._created}")

    def _is_expired(self, conn: Connection) -> bool:
        """检查连接是否过期"""
        return (time.time() - conn.last_used) > self._ttl

    async def acquire(self) -> Optional[Connection]:
        """获取连接"""
        try:
            conn = await asyncio.wait_for(self._pool.get(), timeout=5.0)
            if conn and self._is_expired(conn):
                conn = await self._create_connection()
            return conn
        except asyncio.TimeoutError:
            # 超时，创建新连接
            return await self._create_connection()

    async def release(self, conn: Optional[Connection]) -> None:
        """释放连接回池"""
        if conn is None:
            return
        conn.last_used = time.time()
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
        """当前池大小"""
        return self._pool.qsize()

    @property
    def available(self) -> int:
        """可用连接数"""
        return self._max_size - self._pool.qsize()


# 全局实例
parallel_executor = ParallelExecutor(max_workers=10)
thread_pool = ThreadPoolTaskQueue(max_workers=4)
