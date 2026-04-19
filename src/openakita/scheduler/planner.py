"""
参谋部 (Planner) - 任务分解与路径规划

职责：
- 任务分解（将自然语言任务转为 DAG）
- 路径规划
- 输出 MissionPlan 对象
- 依赖关系管理

禁止：
- 不派发任务
- 不执行任务
"""

import asyncio
import logging
import uuid
from typing import Any

from ..config import settings
from .models import UserRequest, MissionPlan, MissionTask

logger = logging.getLogger(__name__)


class Planner:
    """参谋部 - 负责任务分解和规划"""

    def __init__(self):
        self._strategy_templates = self._initialize_strategies()

    def _initialize_strategies(self) -> list[dict[str, Any]]:
        """初始化策略模板"""
        return [
            {
                "name": "default",
                "description": "默认策略 - 直接执行",
                "use_fast_model": False,
                "skip_non_core": False,
            },
            {
                "name": "fast",
                "description": "快速策略 - 使用更快模型",
                "use_fast_model": True,
                "skip_non_core": True,
            },
            {
                "name": "conservative",
                "description": "保守策略 - 每步确认",
                "use_fast_model": False,
                "skip_non_core": False,
                "step_by_step_confirm": True,
            },
        ]

    async def decompose_task(self, request: UserRequest) -> MissionPlan:
        """
        将用户请求分解为任务 DAG

        Args:
            request: 用户请求

        Returns:
            MissionPlan: 任务计划
        """
        logger.info(f"Decomposing task: request_id={request.request_id}")

        mission_id = f"mission_{request.request_id}"

        # 创建基础任务（简单版本，后续可扩展为真实的 LLM 任务分解）
        main_task = MissionTask(
            task_id=f"{mission_id}_task_0",
            mission_id=mission_id,
            description=request.content,
            max_steps=getattr(settings, "soldier_max_steps", 10),
            timeout_seconds=getattr(settings, "soldier_timeout_seconds", 300),
            priority=request.priority,
        )

        plan = MissionPlan(
            mission_id=mission_id,
            tasks=[main_task],
            dag={main_task.task_id: []},
            strategies=self._strategy_templates.copy(),
            current_strategy_index=0,
        )

        logger.info(f"Plan created: mission_id={mission_id}, tasks={len(plan.tasks)}")
        return plan

    async def optimize_plan(self, plan: MissionPlan) -> MissionPlan:
        """
        优化任务计划

        Args:
            plan: 原始计划

        Returns:
            MissionPlan: 优化后的计划
        """
        # TODO: 实现计划优化逻辑
        # 可以包括：
        # - 任务并行化优化
        # - 依赖关系优化
        # - 资源分配优化
        return plan

    async def generate_fallback_plan(
        self, original_plan: MissionPlan, failure_context: dict[str, Any]
    ) -> MissionPlan:
        """
        生成降级计划

        Args:
            original_plan: 原始计划
            failure_context: 失败上下文信息

        Returns:
            MissionPlan: 降级后的计划
        """
        logger.info(
            f"Generating fallback plan: mission_id={original_plan.mission_id}"
        )

        # 简单版本：复用原计划，但调整策略索引
        new_plan = MissionPlan(
            mission_id=original_plan.mission_id,
            tasks=original_plan.tasks,
            dag=original_plan.dag,
            strategies=original_plan.strategies,
            current_strategy_index=min(
                original_plan.current_strategy_index + 1,
                len(original_plan.strategies) - 1,
            ),
        )

        # 更新任务的策略索引
        for task in new_plan.tasks:
            task.strategy_index = new_plan.current_strategy_index

        return new_plan

    async def decompose_with_llm(self, request: UserRequest) -> MissionPlan:
        """
        使用 LLM 进行智能任务分解（TODO: 待实现）

        Args:
            request: 用户请求

        Returns:
            MissionPlan: 智能分解后的任务计划
        """
        # TODO: 调用 LLM 进行真实的任务分解
        # 这是一个占位实现，未来版本会添加
        logger.warning("LLM-based decomposition not implemented yet, using simple fallback")
        return await self.decompose_task(request)
