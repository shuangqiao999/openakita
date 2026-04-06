"""
并行执行器 - 支持工具、搜索、I/O 并行
"""

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ParallelTask:
    """并行任务定义"""

    name: str
    coro: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    timeout: float = 30.0
    depends_on: list[str] = field(default_factory=list)
    fallback: Callable | None = None


class ParallelExecutor:
    """并行执行器"""

    def __init__(self, max_workers: int = 4, max_concurrent: int = 10):
        self.max_workers = max_workers
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._thread_pool = ThreadPoolExecutor(max_workers=max_workers)

    async def execute_parallel(self, tasks: list[ParallelTask]) -> dict[str, Any]:
        """
        并行执行多个任务

        Args:
            tasks: 任务列表

        Returns:
            {task_name: result} 字典
        """
        if not tasks:
            return {}

        groups = self._group_by_dependencies(tasks)

        results = {}
        for group in groups:
            group_results = await self._execute_group(group, results)
            results.update(group_results)

        return results

    def _group_by_dependencies(self, tasks: list[ParallelTask]) -> list[list[ParallelTask]]:
        """按依赖关系分组"""
        independent = [t for t in tasks if not t.depends_on]
        dependent = [t for t in tasks if t.depends_on]

        groups = []
        if independent:
            groups.append(independent)
        if dependent:
            groups.append(dependent)

        return groups

    async def _execute_group(
        self, tasks: list[ParallelTask], previous_results: dict
    ) -> dict[str, Any]:
        """执行一组并行任务"""

        async def _run_one(task: ParallelTask):
            async with self._semaphore:
                return await self._execute_one(task, previous_results)

        results_list = await asyncio.gather(
            *[_run_one(task) for task in tasks], return_exceptions=True
        )

        results = {}
        for task, result in zip(tasks, results_list, strict=True):
            if isinstance(result, Exception):
                logger.error(f"Task {task.name} failed: {result}")
                if task.fallback:
                    try:
                        fallback_result = await task.fallback()
                        results[task.name] = fallback_result
                    except Exception as e:
                        logger.error(f"Fallback for {task.name} also failed: {e}")
                        results[task.name] = {"error": str(result)}
                else:
                    results[task.name] = {"error": str(result)}
            else:
                results[task.name] = result

        return results

    async def _execute_one(self, task: ParallelTask, previous_results: dict) -> Any:
        """执行单个任务"""
        try:
            resolved_args = self._resolve_dependencies(task.args, previous_results)
            resolved_kwargs = self._resolve_dependencies(task.kwargs, previous_results)

            result = await asyncio.wait_for(
                task.coro(*resolved_args, **resolved_kwargs), timeout=task.timeout
            )
            return result
        except TimeoutError:
            raise Exception(f"Task {task.name} timeout after {task.timeout}s")

    def _resolve_dependencies(self, obj: Any, previous_results: dict) -> Any:
        """解析对象中的依赖引用"""
        if isinstance(obj, dict):
            return {k: self._resolve_dependencies(v, previous_results) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_dependencies(item, previous_results) for item in obj]
        elif isinstance(obj, str) and obj.startswith("$ref:"):
            ref_path = obj[5:].split(".")
            task_name = ref_path[0]
            if task_name in previous_results:
                result = previous_results[task_name]
                for field in ref_path[1:]:
                    if isinstance(result, dict):
                        result = result.get(field, {})
                return result
            return obj
        else:
            return obj

    async def shutdown(self):
        """关闭执行器"""
        self._thread_pool.shutdown(wait=True)


class ParallelSearchExecutor:
    """并行搜索执行器 - 同时查询多个搜索引擎"""

    def __init__(self, timeout: int = 10, max_engines: int = 5):
        self.timeout = timeout
        self.max_engines = max_engines

    async def search_all(self, query: str) -> dict[str, Any]:
        """并行查询所有可用搜索引擎"""
        engines = self._get_available_engines()

        tasks = []
        for engine in engines[: self.max_engines]:
            task = self._search_engine(engine, query)
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_results = []
        for engine, result in zip(engines[: self.max_engines], results, strict=True):
            if isinstance(result, Exception):
                logger.warning(f"Engine {engine} failed: {result}")
            else:
                all_results.extend(result)

        return self._deduplicate(all_results)

    def _get_available_engines(self) -> list[str]:
        """获取可用的搜索引擎列表"""
        return ["duckduckgo", "bing", "baidu", "yandex", "mojeek"]

    async def _search_engine(self, engine: str, query: str) -> list[dict]:
        """查询单个搜索引擎"""
        return []

    def _deduplicate(self, results: list[dict]) -> list[dict]:
        """去重并排序"""
        seen = set()
        unique = []
        for item in results:
            url = item.get("url", "")
            if url and url not in seen:
                seen.add(url)
                unique.append(item)
        return unique
