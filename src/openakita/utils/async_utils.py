import asyncio
import logging
from typing import Set

logger = logging.getLogger(__name__)


def drain_loop_tasks(loop: asyncio.AbstractEventLoop, timeout: float = 3.0) -> None:
    """Cancel all pending tasks on *loop* and run them to completion.

    Use this on a loop that is about to be closed (e.g. a thread-owned
    event loop) to prevent ``Task was destroyed but it is pending!``
    warnings from Python's GC.
    """
    try:
        pending = asyncio.all_tasks(loop)
    except RuntimeError:
        return
    if not pending:
        return
    for task in pending:
        task.cancel()
    try:
        loop.run_until_complete(
            asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout,
            )
        )
    except Exception:
        pass


async def drain_running_loop_tasks(timeout: float = 3.0) -> None:
    """Cancel all pending tasks on the *currently running* loop and await them.

    Use this at the end of a shutdown sequence on the main event loop.
    """
    pending = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
    if not pending:
        return
    for task in pending:
        task.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=timeout,
        )
    except Exception:
        pass


class BgTaskSet:
    """A self-cleaning set of background ``asyncio.Task`` objects.

    Tasks added via ``.add()`` or ``.create_task()`` are automatically
    removed when they finish (via ``add_done_callback``).  Call
    ``.cancel_all()`` during shutdown to drain any still-pending tasks.
    """

    def __init__(self):
        self._tasks: Set[asyncio.Task] = set()

    def add(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def create_task(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.add(task)
        return task

    def discard(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)

    async def cancel_all(self, timeout: float = 3.0) -> None:
        remaining = list(self._tasks)
        if not remaining:
            return
        for task in remaining:
            task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*remaining, return_exceptions=True),
                timeout=timeout,
            )
        except Exception:
            pass
        self._tasks.clear()
