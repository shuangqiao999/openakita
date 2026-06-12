"""
动态 Benchmark 任务生成器

功能:
1. 对高成功率任务生成更难变体
2. 任务质量验证(动作动词+可验证预期)
3. SimHash 去重
4. 任务池容量管理(8-30 个)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TASKS = 30
_DEFAULT_MUTATION_THRESHOLD = 0.95
_DEFAULT_MAX_TIMEOUT = 1800
_DEFAULT_SIMILARITY_THRESHOLD = 0.8

_ACTION_VERBS_RE = re.compile(
    r"创建|编写|搜索|计算|修复|重构|查找|记住|抓取|读取|运行|执行|修改|列出|下载|生成"
    r"|create|write|search|compute|fix|refactor|find|remember|fetch|read|run|exec",
    re.I,
)
_VERIFIABLE_RE = re.compile(
    r"['\"][^'\"]{2,}['\"]|\b\d{2,}\b|文件.*内容|输出.*为|应该.*包含|返回.*至少|成功.*存储"
)


class DynamicBenchmarkGenerator:
    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self._brain = getattr(agent, "brain", None)
        self._variant_counters: dict[str, int] = {}

    # ── 验证器 ──

    @staticmethod
    def validate_task(desc: str, expected: str, timeout: int) -> tuple[bool, str]:
        if not re.search(_ACTION_VERBS_RE, desc):
            return False, "缺少动作动词"
        if not re.search(_VERIFIABLE_RE, expected):
            return False, "预期结果不可验证"
        if not (10 <= timeout <= _DEFAULT_MAX_TIMEOUT):
            return False, f"超时不合理: {timeout}s"
        return True, ""

    # ── SimHash 去重 ──

    @staticmethod
    def _simhash(text: str) -> str:
        words = re.findall(r"\b\w+\b", text.lower())
        return hashlib.md5(" ".join(sorted(set(words))).encode()).hexdigest()

    def _is_duplicate(self, new_desc: str, existing: list[Any]) -> bool:
        new_hash = self._simhash(new_desc)
        for task in existing:
            desc = getattr(task, "description", str(task))
            h = self._simhash(desc)
            if h == new_hash:
                return True
        return False

    # ── 变体生成 ──

    def _next_variant_id(self, base_id: str) -> int:
        self._variant_counters[base_id] = self._variant_counters.get(base_id, 0) + 1
        return self._variant_counters[base_id]

    async def generate_harder_variant(self, task: Any) -> Any | None:
        if not self._brain:
            return None

        timeout = min(getattr(task, "timeout_seconds", 120) * 2, _DEFAULT_MAX_TIMEOUT)
        prompt = f"""你是 Benchmark 设计专家。当前任务 Agent 已接近满分，需升级难度。
原任务: {getattr(task, "description", str(task))}
预期: {getattr(task, "expected_outcome", "")}
超时: {timeout}s

生成更难变体。JSON:
{{"description": "新任务(含动作动词)", "expected_outcome": "可验证预期(含具体数值/文件/关键词)", "difficulty": "hard", "timeout_seconds": {timeout}}}
"""
        try:
            response = await self._brain.think(prompt)
            data = json.loads(_strip_json(response))
            ok, reason = self.validate_task(
                data["description"],
                data.get("expected_outcome", ""),
                data.get("timeout_seconds", timeout),
            )
            if not ok:
                logger.warning("[DynamicBench] 验证失败: %s", reason)
                return None
            vid = self._next_variant_id(task.id)
            return replace(
                task,
                id=f"{task.id}-v{vid}",
                description=data["description"],
                expected_outcome=data.get("expected_outcome", task.expected_outcome),
                difficulty=data.get("difficulty", "hard"),
                timeout_seconds=data.get("timeout_seconds", timeout),
            )
        except Exception as e:
            logger.warning("[DynamicBench] 生成失败: %s", e)
            return None

    # ── 任务池维护 ──

    async def maintain_task_pool(
        self, tasks: list[Any], success_history: dict[str, float]
    ) -> list[Any]:
        from openakita.config import settings

        max_tasks = getattr(settings, "dynamic_benchmark_max_tasks", _DEFAULT_MAX_TASKS)
        threshold = getattr(
            settings, "dynamic_benchmark_mutation_threshold", _DEFAULT_MUTATION_THRESHOLD
        )
        result = list(tasks)

        for task in tasks:
            rate = success_history.get(task.id, 0)
            if rate >= threshold:
                variant = await self.generate_harder_variant(task)
                if variant and not self._is_duplicate(
                    variant.description, result + [t for t in tasks if t.id != task.id]
                ):
                    result.append(variant)
                    logger.info("[DynamicBench] 新变体: %s", variant.id)

        if len(result) > max_tasks:
            sorted_tasks = sorted(result, key=lambda t: success_history.get(t.id, 0), reverse=True)
            discarded = sorted_tasks[max_tasks:]
            result = sorted_tasks[:max_tasks]
            for t in discarded:
                logger.info(
                    "[DynamicBench] 淘汰: %s (成功率=%.0f%%)",
                    t.id,
                    success_history.get(t.id, 0) * 100,
                )

        return result


def _strip_json(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    text = re.sub(r"\n?```\s*$", "", text)
    s = text.find("{")
    if s == -1:
        s = text.find("[")
    if s > 0:
        text = text[s:]
    e = max(text.rfind("}"), text.rfind("]"))
    if 0 <= e < len(text) - 1:
        text = text[: e + 1]
    return text


def save_tasks_to_file(tasks: list[Any], path: Path) -> None:
    data = [
        {
            "id": t.id,
            "description": t.description,
            "category": t.category,
            "expected_outcome": t.expected_outcome,
            "timeout_seconds": t.timeout_seconds,
            "difficulty": t.difficulty,
        }
        for t in tasks
    ]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
