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

from ._utils import strip_json

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
    def validate_task(desc: str, expected: str, timeout: int, *, category: str = "") -> tuple[bool, str]:
        if not re.search(_ACTION_VERBS_RE, desc):
            return False, "缺少动作动词"
        if not expected or len(expected) < 10:
            return False, "预期结果过短/不可验证"
        if not re.search(_VERIFIABLE_RE, expected):
            return False, "预期结果不可验证"
        if not (10 <= timeout <= _DEFAULT_MAX_TIMEOUT):
            return False, f"超时不合理: {timeout}s"
        if category == "coding":
            if not re.search(r"代码|成功|无报错|测试|通过|输出|返回", expected):
                return False, "coding任务缺少验证词"
        return True, ""

    # ── SimHash 去重 ──

    @staticmethod
    def _simhash(text: str) -> str:
        from openakita.core.tokenizer import segment_text
        words = sorted(set(segment_text(text.lower()).split()))
        return hashlib.md5(" ".join(words).encode()).hexdigest()

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
        # 剥离已有 -v\d+ 后缀, 取 root ID, 防止 -v1-v1-v1 链式增长
        root_id = re.sub(r"(-v\d+)+$", "", base_id)
        self._variant_counters[root_id] = self._variant_counters.get(root_id, 0) + 1
        return self._variant_counters[root_id]

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
            data = json.loads(strip_json(response.content))
            ok, reason = self.validate_task(
                data["description"],
                data.get("expected_outcome", ""),
                data.get("timeout_seconds", timeout),
            )
            if not ok:
                logger.warning("[DynamicBench] 验证失败: %s", reason)
                return None
            vid = self._next_variant_id(task.id)
            root_id = re.sub(r"(-v\d+)+$", "", task.id)
            return replace(
                task,
                id=f"{root_id}-v{vid}",
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
                if variant:
                    # 前缀去重: 已有同 base task 的任何 variant 则跳过
                    prefix = f"{task.id}-v"
                    if any(getattr(t, "id", str(t)).startswith(prefix) for t in result):
                        logger.debug("[DynamicBench] 已有变体, 跳过: %s", task.id)
                    elif not self._is_duplicate(
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

    async def generate_from_traces(self, max_tasks: int = 3) -> list[dict]:
        """从最近成功会话中提取用户任务类型，生成场景化 benchmark 任务"""
        if not self._brain:
            return []

        traces = self._load_successful_traces(30)
        if len(traces) < 5:
            logger.info("[DynamicBench] 成功会话不足(%d条)，跳过场景化生成", len(traces))
            return []

        user_messages = []
        for t in traces:
            for step in t.get("iterations", []):
                msg = step.get("user_message", step.get("query", ""))
                if msg:
                    user_messages.append(msg[:200])
                    break

        if not user_messages:
            return []

        prompt = f"""从以下用户真实对话中识别最常见的任务类型，为每种类型生成一个 Benchmark 任务:

用户消息样本:
{chr(10).join(f'- {m}' for m in user_messages[:20])}

输出 JSON 数组:
[{{"category": "coding/research/writing/tool_use/memory", "description": "具体任务描述", "expected_outcome": "可验证的预期结果(含具体数值/关键词)", "difficulty": "medium", "timeout_seconds": 300}}]

最多生成 {max_tasks} 个不同类别的任务。只输出 JSON 数组，不要解释。"""
        try:
            import json as _json

            response = await self._brain.think(prompt)
            tasks = _json.loads(strip_json(response.content))
            if not isinstance(tasks, list):
                return []
            valid = [t for t in tasks if self._is_task_valid(t)]
            skipped = len(tasks) - len(valid)
            if skipped:
                logger.warning("[DynamicBench] 过滤 %d 个无效场景化任务", skipped)
            return valid[:max_tasks]
        except Exception as e:
            logger.warning("[DynamicBench] 场景化任务生成失败: %s", e)
            return []

    def _load_successful_traces(self, limit: int = 30) -> list[dict]:
        from openakita.config import settings

        traces = []
        traces_dir = settings.data_dir / "react_traces"
        if not traces_dir.is_dir():
            return traces

        files = []
        for d in traces_dir.iterdir():
            if d.is_dir():
                files.extend(d.glob("*.json"))
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        import json as _json

        for f in files[:limit]:
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))
                if data.get("result") in ("success", "completed"):
                    traces.append(data)
            except Exception:
                continue
        return traces


    @staticmethod
    def _is_task_valid(task: dict) -> bool:
        desc = task.get("description", "")
        expected = task.get("expected_outcome", "")
        timeout = task.get("timeout_seconds", 300)
        category = task.get("category", "")
        ok, reason = DynamicBenchmarkGenerator.validate_task(desc, expected, timeout, category=category)
        if not ok:
            logger.warning("[DynamicBench] 任务验证失败: %s — %s", reason, desc[:50])
        return ok


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
