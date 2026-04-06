"""
异步任务管理器 - 解决 asyncio Task exception was never retrieved 错误
"""

import asyncio
import logging
import traceback
from collections.abc import Callable
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class AsyncTaskManager:
    """
    安全的异步任务管理器
    - 自动捕获 Task 异常
    - 防止 Task 被垃圾回收时未处理异常
    - 支持优雅关闭
    """

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = True

    def create_task(self, coro, name: str = None, on_error: Callable = None) -> asyncio.Task:
        """
        安全创建 Task，自动处理异常

        Args:
            coro: 协程对象
            name: 任务名称
            on_error: 错误回调函数
        """
        task = asyncio.create_task(coro, name=name)
        task_name = name or f"task_{id(task)}"
        self._tasks[task_name] = task

        def _handle_exception(t: asyncio.Task):
            if t.done() and not t.cancelled():
                try:
                    exc = t.exception()
                    if exc is not None:
                        logger.error(f"Task {task_name} failed: {exc}\n{traceback.format_exc()}")
                        if on_error:
                            on_error(exc)
                except asyncio.CancelledError:
                    pass
                finally:
                    if task_name in self._tasks:
                        del self._tasks[task_name]

        task.add_done_callback(_handle_exception)
        return task

    async def cancel_all(self, timeout: float = 5.0):
        """取消所有正在运行的任务"""
        logger.info(f"Cancelling {len(self._tasks)} tasks...")

        for _name, task in self._tasks.items():
            if not task.done():
                task.cancel()

        if self._tasks:
            await asyncio.wait(self._tasks.values(), timeout=timeout)

        self._tasks.clear()

    def get_task_count(self) -> int:
        """获取活跃任务数量"""
        to_remove = []
        for _name, task in self._tasks.items():
            if task.done():
                to_remove.append(_name)
        for name in to_remove:
            del self._tasks[name]

        return len(self._tasks)


@asynccontextmanager
async def safe_task_context():
    """安全的任务上下文管理器"""
    manager = AsyncTaskManager()
    try:
        yield manager
    finally:
        await manager.cancel_all()


class SafeWebSocketHandler:
    """
    安全的 WebSocket 处理器
    - 自动重连
    - 防止连接泄露
    """

    def __init__(self, url: str, on_message: Callable, max_reconnect_attempts: int = 5):
        self.url = url
        self.on_message = on_message
        self.max_reconnect_attempts = max_reconnect_attempts
        self._ws = None
        self._running = False
        self._reconnect_count = 0
        self._task = None

    async def start(self):
        """启动 WebSocket 连接"""
        self._running = True
        self._task = asyncio.create_task(self._run())
        return self._task

    async def _run(self):
        """主循环，支持自动重连"""
        while self._running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                if not await self._should_reconnect():
                    break

    async def _connect_and_listen(self):
        """连接并监听消息"""
        import websockets

        async with websockets.connect(self.url) as ws:
            self._ws = ws
            self._reconnect_count = 0
            logger.info(f"WebSocket connected to {self.url}")

            async for message in ws:
                if not self._running:
                    break
                try:
                    await self.on_message(message)
                except Exception as e:
                    logger.error(f"Message handler error: {e}")

    async def _should_reconnect(self) -> bool:
        """判断是否应该重连"""
        if not self._running:
            return False

        if self._reconnect_count >= self.max_reconnect_attempts:
            logger.error(f"Max reconnect attempts ({self.max_reconnect_attempts}) reached")
            return False

        self._reconnect_count += 1
        delay = min(2**self._reconnect_count, 30)
        logger.info(f"Reconnecting in {delay}s (attempt {self._reconnect_count})")
        await asyncio.sleep(delay)
        return True

    async def stop(self):
        """停止 WebSocket 连接"""
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
