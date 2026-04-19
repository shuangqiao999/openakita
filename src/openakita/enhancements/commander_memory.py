"""
指挥官记忆扩展 — 深度复用现有记忆系统

复用现有记忆系统，为指挥官增加记忆能力：
- 任务记忆：完整任务的输入、输出、状态、策略路径
- 步骤记忆：每个执行步骤的详情
- 反馈记忆：失败时的错误类型和调整措施
- 经验记忆："在什么情况下用什么策略会成功"
- 反思记忆：任务完成后的复盘总结
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from ..memory.manager import MemoryManager
from ..memory.types import (
    SemanticMemory,
    MemoryType,
    MemoryPriority,
)

logger = logging.getLogger(__name__)


class MemorySubjectType(Enum):
    """记忆主体类型"""

    AGENT = "agent"
    COMMANDER = "commander"


class CommanderMemoryType(Enum):
    """指挥官记忆类型"""

    TASK = "task"  # 任务记忆：完整任务的输入、输出、状态、策略路径
    STEP = "step"  # 步骤记忆：每个执行步骤的详情
    FEEDBACK = "feedback"  # 反馈记忆：失败时的错误类型和调整措施
    EXPERIENCE = "experience"  # 经验记忆："在什么情况下用什么策略会成功"
    REFLECTION = "reflection"  # 反思记忆：任务完成后的复盘总结


@dataclass
class CommanderMemoryRecord:
    """指挥官记忆记录"""

    record_id: str
    subject_type: MemorySubjectType = MemorySubjectType.COMMANDER
    memory_type: CommanderMemoryType = CommanderMemoryType.TASK
    mission_id: str = ""
    task_id: str = ""
    content: str = ""
    trust_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class StrategyRecommendation:
    """策略推荐结果"""
    strategy: str
    confidence: float
    source_memory_id: str
    source_task_id: str


class CommanderMemoryExtension:
    """
    指挥官记忆扩展 — 深度复用现有记忆系统

    利用现有的 MemoryManager 来存储和检索指挥官的记忆。
    """

    def __init__(self, memory_manager: MemoryManager | None = None):
        self._memory_manager: MemoryManager | None = memory_manager

        if self._memory_manager:
            logger.info("CommanderMemoryExtension initialized with existing MemoryManager")
        else:
            logger.warning(
                "CommanderMemoryExtension initialized without MemoryManager. "
                "Call set_memory_manager() before using."
            )

    def set_memory_manager(self, memory_manager: MemoryManager) -> None:
        """设置记忆管理器"""
        self._memory_manager = memory_manager
        logger.info("MemoryManager set for CommanderMemoryExtension")

    def _ensure_memory_manager(self) -> MemoryManager:
        """确保记忆管理器可用"""
        if not self._memory_manager:
            raise RuntimeError(
                "MemoryManager not set. Call set_memory_manager() first."
            )
        return self._memory_manager

    def _generate_id(self) -> str:
        """生成唯一ID"""
        return f"cmd_mem_{uuid.uuid4().hex[:16]}"

    async def record_task_start(
        self,
        mission_id: str,
        task_id: str,
        task_input: str,
    ) -> None:
        """记录任务开始"""
        try:
            mm = self._ensure_memory_manager()
            memory_id = self._generate_id()
            
            content = (
                f"任务开始: mission_id={mission_id}, task_id={task_id}\n"
                f"任务输入: {task_input}"
            )
            
            mem = SemanticMemory(
                id=memory_id,
                type=MemoryType.EXPERIENCE,
                priority=MemoryPriority.LONG_TERM,
                content=content,
                source="commander",
                subject="commander",
                predicate="task_start",
                tags=["commander", "task", "start"],
                importance_score=0.6,
            )
            
            mem.metadata = {
                "subject_type": MemorySubjectType.COMMANDER.value,
                "mission_id": mission_id,
                "task_id": task_id,
            }
            
            mm.add_memory(mem)
            logger.debug(f"Recorded task start: {mission_id}/{task_id}")
            
        except Exception as e:
            logger.error(f"Failed to record task start: {e}", exc_info=True)

    async def record_step_complete(
        self,
        mission_id: str,
        task_id: str,
        step_number: int,
        step_input: dict[str, Any],
        step_output: Any,
        success: bool,
    ) -> None:
        """记录步骤完成"""
        try:
            mm = self._ensure_memory_manager()
            memory_id = self._generate_id()
            
            status = "成功" if success else "失败"
            content = (
                f"步骤完成: mission_id={mission_id}, task_id={task_id}, step={step_number}\n"
                f"状态: {status}\n"
                f"步骤输入: {step_input}\n"
                f"步骤输出: {step_output}"
            )
            
            mem = SemanticMemory(
                id=memory_id,
                type=MemoryType.EXPERIENCE,
                priority=MemoryPriority.SHORT_TERM,
                content=content,
                source="commander",
                subject="commander",
                predicate="step_complete",
                tags=["commander", "step", "complete", status.lower()],
                importance_score=0.5,
            )
            
            mm.add_memory(mem)
            logger.debug(
                f"Recorded step complete: {mission_id}/{task_id} step {step_number}"
            )
            
        except Exception as e:
            logger.error(f"Failed to record step complete: {e}", exc_info=True)

    async def record_task_success(
        self,
        mission_id: str,
        task_id: str,
        strategy_used: str,
        final_output: Any,
        trust_score: float = 0.0,
    ) -> None:
        """记录任务成功"""
        try:
            mm = self._ensure_memory_manager()
            memory_id = self._generate_id()
            
            content = (
                f"任务成功: mission_id={mission_id}, task_id={task_id}\n"
                f"使用策略: {strategy_used}\n"
                f"最终输出: {final_output}"
            )
            
            mem = SemanticMemory(
                id=memory_id,
                type=MemoryType.EXPERIENCE,
                priority=MemoryPriority.LONG_TERM,
                content=content,
                source="commander",
                subject="commander",
                predicate="task_success",
                tags=["commander", "task", "success", strategy_used],
                importance_score=0.8,
            )
            
            mm.add_memory(mem)
            logger.debug(f"Recorded task success: {mission_id}/{task_id}")
            
            await self._record_experience(
                mission_id=mission_id,
                task_id=task_id,
                success=True,
                strategy=strategy_used,
                context=content,
                trust_score=trust_score,
            )
            
        except Exception as e:
            logger.error(f"Failed to record task success: {e}", exc_info=True)

    async def record_task_failure(
        self,
        mission_id: str,
        task_id: str,
        error_type: str,
        error_details: str,
        adjustment_taken: str | None = None,
    ) -> None:
        """记录任务失败"""
        try:
            mm = self._ensure_memory_manager()
            memory_id = self._generate_id()
            
            content = (
                f"任务失败: mission_id={mission_id}, task_id={task_id}\n"
                f"错误类型: {error_type}\n"
                f"错误详情: {error_details}"
            )
            if adjustment_taken:
                content += f"\n调整措施: {adjustment_taken}"
            
            mem = SemanticMemory(
                id=memory_id,
                type=MemoryType.ERROR,
                priority=MemoryPriority.LONG_TERM,
                content=content,
                source="commander",
                subject="commander",
                predicate="task_failure",
                tags=["commander", "task", "failure", error_type],
                importance_score=0.7,
            )
            
            mm.add_memory(mem)
            logger.debug(f"Recorded task failure: {mission_id}/{task_id}")
            
            await self._record_feedback(
                mission_id=mission_id,
                task_id=task_id,
                error_type=error_type,
                error_details=error_details,
                adjustment_taken=adjustment_taken,
            )
            
        except Exception as e:
            logger.error(f"Failed to record task failure: {e}", exc_info=True)

    async def record_reflection(
        self,
        mission_id: str,
        task_id: str,
        reflection_content: str,
    ) -> None:
        """记录反思"""
        try:
            mm = self._ensure_memory_manager()
            memory_id = self._generate_id()
            
            content = (
                f"任务反思: mission_id={mission_id}, task_id={task_id}\n"
                f"反思内容: {reflection_content}"
            )
            
            mem = SemanticMemory(
                id=memory_id,
                type=MemoryType.EXPERIENCE,
                priority=MemoryPriority.PERMANENT,
                content=content,
                source="commander",
                subject="commander",
                predicate="reflection",
                tags=["commander", "reflection"],
                importance_score=0.9,
            )
            
            mm.add_memory(mem)
            logger.debug(f"Recorded reflection: {mission_id}/{task_id}")
            
        except Exception as e:
            logger.error(f"Failed to record reflection: {e}", exc_info=True)

    async def _record_experience(
        self,
        mission_id: str,
        task_id: str,
        success: bool,
        strategy: str,
        context: str,
        trust_score: float = 0.0,
    ) -> None:
        """记录经验记忆"""
        try:
            mm = self._ensure_memory_manager()
            memory_id = self._generate_id()
            
            outcome = "成功" if success else "失败"
            content = (
                f"经验总结: 在类似任务中使用策略 '{strategy}' {outcome}\n"
                f"上下文: {context}"
            )
            
            mem = SemanticMemory(
                id=memory_id,
                type=MemoryType.EXPERIENCE,
                priority=MemoryPriority.LONG_TERM,
                content=content,
                source="commander",
                subject="commander",
                predicate="experience",
                tags=["commander", "experience", strategy, outcome.lower()],
                importance_score=0.85,
            )
            
            mm.add_memory(mem)
            
        except Exception as e:
            logger.error(f"Failed to record experience: {e}", exc_info=True)

    async def _record_feedback(
        self,
        mission_id: str,
        task_id: str,
        error_type: str,
        error_details: str,
        adjustment_taken: str | None = None,
    ) -> None:
        """记录反馈记忆"""
        try:
            mm = self._ensure_memory_manager()
            memory_id = self._generate_id()
            
            content = (
                f"反馈记录: 遇到错误类型 '{error_type}'\n"
                f"错误详情: {error_details}"
            )
            if adjustment_taken:
                content += f"\n调整措施: {adjustment_taken}"
            
            mem = SemanticMemory(
                id=memory_id,
                type=MemoryType.EXPERIENCE,
                priority=MemoryPriority.LONG_TERM,
                content=content,
                source="commander",
                subject="commander",
                predicate="feedback",
                tags=["commander", "feedback", error_type],
                importance_score=0.75,
            )
            
            mm.add_memory(mem)
            
        except Exception as e:
            logger.error(f"Failed to record feedback: {e}", exc_info=True)

    async def retrieve_similar_success_tasks(
        self,
        task_description: str,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """检索相似的成功任务"""
        try:
            mm = self._ensure_memory_manager()
            
            memories = mm.search_memories(
                query=task_description,
                memory_type=MemoryType.EXPERIENCE,
                tags=["commander", "task", "success"],
                limit=top_k * 2,
            )
            
            results = []
            for mem in memories:
                if len(results) >= top_k:
                    break
                results.append({
                    "memory_id": mem.id,
                    "content": mem.content,
                    "importance_score": mem.importance_score,
                    "created_at": mem.created_at,
                })
            
            logger.debug(
                f"Retrieved {len(results)} similar success tasks"
            )
            return results
            
        except Exception as e:
            logger.error(f"Failed to retrieve similar success tasks: {e}", exc_info=True)
            return []

    async def retrieve_similar_failure_tasks(
        self,
        task_description: str,
    ) -> list[dict[str, Any]]:
        """检索相似的失败任务"""
        try:
            mm = self._ensure_memory_manager()
            
            memories = mm.search_memories(
                query=task_description,
                memory_type=MemoryType.ERROR,
                tags=["commander", "task", "failure"],
                limit=5,
            )
            
            exp_memories = mm.search_memories(
                query=task_description,
                memory_type=MemoryType.EXPERIENCE,
                tags=["commander", "experience", "failure"],
                limit=5,
            )
            
            all_memories = memories + exp_memories
            all_memories.sort(key=lambda m: m.importance_score, reverse=True)
            
            results = []
            for mem in all_memories[:10]:
                results.append({
                    "memory_id": mem.id,
                    "content": mem.content,
                    "importance_score": mem.importance_score,
                    "created_at": mem.created_at,
                })
            
            logger.debug(
                f"Retrieved {len(results)} similar failure tasks"
            )
            return results
            
        except Exception as e:
            logger.error(f"Failed to retrieve similar failure tasks: {e}", exc_info=True)
            return []

    async def get_strategy_recommendation(
        self,
        task_description: str,
    ) -> StrategyRecommendation | None:
        """获取策略推荐（返回结构化结果）"""
        try:
            success_tasks = await self.retrieve_similar_success_tasks(
                task_description, top_k=3
            )
            
            if not success_tasks:
                return None
            
            best = success_tasks[0]
            
            importance = best.get("importance_score", 0.5)
            confidence = min(0.95, importance * 1.1)
            
            metadata = best.get("metadata", {})
            
            return StrategyRecommendation(
                strategy=best.get("content", ""),
                confidence=confidence,
                source_memory_id=best.get("memory_id", ""),
                source_task_id=metadata.get("task_id", ""),
            )
            
        except Exception as e:
            logger.error(f"Failed to get strategy recommendation: {e}", exc_info=True)
            return None

    async def cleanup_old_memories(
        self,
        days_to_keep: int = 30,
        max_memories: int = 10000,
    ) -> dict[str, int]:
        """
        清理旧记忆
        
        Args:
            days_to_keep: 保留多少天内的记忆
            max_memories: 最大记忆数量
        
        Returns:
            清理统计信息
        """
        try:
            mm = self._ensure_memory_manager()
            
            all_memories = mm.search_memories(
                query="",
                tags=["commander"],
                limit=max_memories * 2,
            )
            
            before_count = len(all_memories)
            deleted_count = 0
            
            cutoff_time = datetime.now() - timedelta(days=days_to_keep)
            
            for mem in all_memories:
                if mem.priority != MemoryPriority.PERMANENT:
                    if mem.created_at < cutoff_time:
                        mm.delete_memory(mem.id)
                        deleted_count += 1
            
            remaining = mm.search_memories(
                query="",
                tags=["commander"],
                limit=max_memories * 2,
            )
            
            if len(remaining) > max_memories:
                to_delete = sorted(
                    [m for m in remaining if m.priority != MemoryPriority.PERMANENT],
                    key=lambda m: m.created_at
                )
                for mem in to_delete[:len(remaining) - max_memories]:
                    mm.delete_memory(mem.id)
                    deleted_count += 1
            
            after_count = len(mm.search_memories(
                query="",
                tags=["commander"],
                limit=max_memories * 2,
            ))
            
            return {
                "before_count": before_count,
                "after_count": after_count,
                "deleted_count": deleted_count,
            }
            
        except Exception as e:
            logger.error(f"Failed to cleanup memories: {e}")
            return {"before_count": 0, "after_count": 0, "deleted_count": 0}

    async def get_memory_stats(self) -> dict[str, Any]:
        """获取记忆统计信息"""
        try:
            mm = self._ensure_memory_manager()
            
            all_memories = mm.search_memories(
                query="",
                tags=["commander"],
                limit=100000,
            )
            
            type_counts = {}
            for mem in all_memories:
                mem_type = mem.type.value
                type_counts[mem_type] = type_counts.get(mem_type, 0) + 1
            
            priority_counts = {}
            for mem in all_memories:
                priority = mem.priority.value
                priority_counts[priority] = priority_counts.get(priority, 0) + 1
            
            avg_importance = sum(m.importance_score for m in all_memories) / len(all_memories) if all_memories else 0
            
            oldest_memory = min(m.created_at for m in all_memories) if all_memories else None
            newest_memory = max(m.created_at for m in all_memories) if all_memories else None
            
            return {
                "total_count": len(all_memories),
                "type_counts": type_counts,
                "priority_counts": priority_counts,
                "avg_importance_score": avg_importance,
                "oldest_memory": oldest_memory,
                "newest_memory": newest_memory,
            }
            
        except Exception as e:
            logger.error(f"Failed to get memory stats: {e}")
            return {"error": str(e)}


# 便捷实例
_commander_memory: CommanderMemoryExtension | None = None


def get_commander_memory() -> CommanderMemoryExtension:
    """获取指挥官记忆扩展单例"""
    global _commander_memory
    if _commander_memory is None:
        _commander_memory = CommanderMemoryExtension()
    return _commander_memory

