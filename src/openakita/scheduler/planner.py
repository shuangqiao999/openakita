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
import json
import logging
import uuid
from typing import Any, Optional, Dict, List

from ..core.brain import Brain
from .models import UserRequest, MissionPlan, MissionTask
from .prompts import (
    TASK_DECOMPOSE_SYSTEM_PROMPT,
    TASK_CLASSIFY_PROMPT,
    SIMPLE_TASK_DECOMPOSE_PROMPT,
)

logger = logging.getLogger(__name__)


class Planner:
    """
    参谋部 - 负责任务分解和规划
    
    功能：
    - 智能任务分解（复用 Brain 的提示词编译能力）
    - 依赖关系分析
    - 并行任务识别
    - 降级计划生成
    
    温度参数在模块内部设置，不依赖外部配置
    """

    def __init__(self, brain: Optional[Brain] = None):
        """
        初始化参谋部
        
        Args:
            brain: Brain 实例（用于 LLM 调用），如果不提供则尝试获取全局实例
        """
        self._brain = brain
        
        # 温度参数 - 在模块内部设置
        self._classification_temperature = 0.1   # 分类任务：低温度保证确定性
        self._simple_decompose_temperature = 0.2  # 简单分解：稍高
        self._complex_decompose_temperature = 0.3 # 复杂分解：平衡创造性和准确性
        
        self._strategy_templates = self._initialize_strategies()
        self._decompose_cache: Dict[str, MissionPlan] = {}
        
        logger.info("Planner initialized")

    def _initialize_strategies(self) -> List[Dict[str, Any]]:
        """初始化策略模板"""
        return [
            {
                "name": "default",
                "description": "默认策略 - 标准执行",
                "skip_non_core": False,
                "max_retries": 3,
            },
            {
                "name": "fast",
                "description": "快速策略 - 跳过非核心步骤",
                "skip_non_core": True,
                "max_retries": 1,
            },
            {
                "name": "conservative",
                "description": "保守策略 - 每步确认",
                "skip_non_core": False,
                "step_by_step_confirm": True,
                "max_retries": 5,
            },
            {
                "name": "fallback",
                "description": "降级策略 - 简化执行",
                "skip_non_core": True,
                "simplify_output": True,
                "max_retries": 2,
            },
        ]

    async def _call_prompt_compiler(self, prompt: str, temperature: float) -> str:
        """
        调用提示词编译端点
        
        复用 Brain 的提示词编译能力
        """
        if not self._brain:
            raise RuntimeError("Brain instance not available in Planner")
        
        # 使用 brain.think_lightweight 或 compiler_think
        if hasattr(self._brain, 'think_lightweight'):
            response = await self._brain.think_lightweight(
                prompt=prompt,
                max_tokens=2048,
            )
        elif hasattr(self._brain, 'compiler_think'):
            response = await self._brain.compiler_think(
                prompt=prompt,
                max_tokens=2048,
            )
        elif hasattr(self._brain, 'think'):
            response = await self._brain.think(
                prompt=prompt,
                max_tokens=2048,
            )
        else:
            raise RuntimeError("No compatible LLM method found in Brain")
        
        if hasattr(response, 'content'):
            return response.content
        return str(response)

    async def decompose_task(self, request: UserRequest) -> MissionPlan:
        """
        将用户请求分解为任务 DAG
        """
        logger.info(f"Decomposing task: request_id={request.request_id}")

        mission_id = f"mission_{request.request_id or uuid.uuid4().hex[:12]}"

        # 步骤1：快速分类
        task_type, complexity, estimated_steps = await self._classify_task(request.content)
        logger.info(f"Task classified: type={task_type}, complexity={complexity}, steps={estimated_steps}")

        # 步骤2：根据复杂度选择分解策略
        if complexity == "simple" and estimated_steps <= 3:
            plan = await self._simple_decompose(mission_id, request)
        else:
            plan = await self._complex_decompose(mission_id, request)

        # 步骤3：验证计划
        if not self._validate_plan(plan):
            logger.warning(f"Plan validation failed for {mission_id}, using fallback")
            plan = await self._fallback_decompose(mission_id, request)

        # 步骤4：添加策略模板
        plan.strategies = self._strategy_templates.copy()
        plan.current_strategy_index = 0

        for task in plan.tasks:
            task.strategy_index = plan.current_strategy_index

        logger.info(f"Plan created: mission_id={mission_id}, tasks={len(plan.tasks)}")
        
        # 缓存
        self._decompose_cache[request.content] = plan

        return plan

    async def _classify_task(self, user_request: str) -> tuple[str, str, int]:
        """快速分类任务"""
        try:
            prompt = TASK_CLASSIFY_PROMPT.format(user_request=user_request[:500])
            
            response = await self._call_prompt_compiler(
                prompt=prompt,
                temperature=self._classification_temperature
            )
            
            data = self._parse_json_response(response)
            
            task_type = data.get("task_type", "other")
            complexity = data.get("complexity", "medium")
            estimated_steps = data.get("estimated_steps", 5)
            
            return task_type, complexity, estimated_steps
            
        except Exception as e:
            logger.error(f"Task classification failed: {e}, using defaults")
            return "other", "medium", 5

    async def _simple_decompose(self, mission_id: str, request: UserRequest) -> MissionPlan:
        """简单任务快速分解"""
        try:
            prompt = SIMPLE_TASK_DECOMPOSE_PROMPT.format(user_request=request.content)
            
            response = await self._call_prompt_compiler(
                prompt=prompt,
                temperature=self._simple_decompose_temperature
            )
            
            task_names = self._parse_json_array(response)
            
            tasks = []
            dag = {}
            prev_task_id = None
            
            for i, name in enumerate(task_names[:5]):
                task_id = f"{mission_id}_task_{i}"
                tasks.append(MissionTask(
                    task_id=task_id,
                    mission_id=mission_id,
                    name=name,
                    description=name,
                    priority=10 - i,
                ))
                
                if prev_task_id:
                    dag[task_id] = [prev_task_id]
                else:
                    dag[task_id] = []
                
                prev_task_id = task_id
            
            return MissionPlan(
                mission_id=mission_id,
                tasks=tasks,
                dag=dag,
            )
            
        except Exception as e:
            logger.error(f"Simple decompose failed: {e}, using fallback")
            return await self._fallback_decompose(mission_id, request)

    async def _complex_decompose(self, mission_id: str, request: UserRequest) -> MissionPlan:
        """复杂任务深度分解"""
        try:
            # 构建包含系统提示词的消息
            full_prompt = f"""{TASK_DECOMPOSE_SYSTEM_PROMPT}

用户请求：{request.content}

请输出 JSON："""
            
            response = await self._call_prompt_compiler(
                prompt=full_prompt,
                temperature=self._complex_decompose_temperature
            )
            
            data = self._parse_json_response(response)
            
            tasks = []
            task_map = {}
            for task_data in data.get("tasks", []):
                task_id = f"{mission_id}_{task_data['id']}"
                task = MissionTask(
                    task_id=task_id,
                    mission_id=mission_id,
                    name=task_data.get("name", "未命名任务"),
                    description=task_data.get("description", ""),
                    priority=task_data.get("priority", 5),
                )
                tasks.append(task)
                task_map[task_data["id"]] = task_id
            
            dag = {task.task_id: [] for task in tasks}
            
            for dep in data.get("dependencies", []):
                task_id = task_map.get(dep["task"])
                if task_id:
                    depends_on = [task_map.get(d) for d in dep.get("depends_on", []) if d in task_map]
                    dag[task_id] = depends_on
            
            return MissionPlan(
                mission_id=mission_id,
                tasks=tasks,
                dag=dag,
            )
            
        except Exception as e:
            logger.error(f"Complex decompose failed: {e}, using fallback")
            return await self._fallback_decompose(mission_id, request)

    async def _fallback_decompose(self, mission_id: str, request: UserRequest) -> MissionPlan:
        """降级分解"""
        logger.warning(f"Using fallback decomposition for {mission_id}")
        
        main_task = MissionTask(
            task_id=f"{mission_id}_task_0",
            mission_id=mission_id,
            name="执行任务",
            description=request.content,
            max_steps=10,
            timeout_seconds=300,
            priority=getattr(request, 'priority', 5),
        )
        
        return MissionPlan(
            mission_id=mission_id,
            tasks=[main_task],
            dag={main_task.task_id: []},
        )

    def _validate_plan(self, plan: MissionPlan) -> bool:
        """验证任务计划"""
        if not plan.tasks:
            return False
        
        task_ids = {task.task_id for task in plan.tasks}
        
        for task_id, deps in plan.dag.items():
            for dep in deps:
                if dep not in task_ids:
                    return False
        
        if self._has_cycle(plan.dag):
            return False
        
        return True

    def _has_cycle(self, dag: Dict[str, List[str]]) -> bool:
        """检测循环依赖"""
        visited = set()
        rec_stack = set()
        
        def dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            
            for neighbor in dag.get(node, []):
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            
            rec_stack.remove(node)
            return False
        
        for node in dag:
            if node not in visited:
                if dfs(node):
                    return True
        return False

    def _parse_json_response(self, response: str) -> dict:
        """解析 JSON 响应"""
        response = response.strip()
        
        # 处理 markdown 代码块
        if "```json" in response:
            start = response.find("```json") + 7
            end = response.find("```", start)
            response = response[start:end].strip()
        elif "```" in response:
            start = response.find("```") + 3
            end = response.find("```", start)
            response = response[start:end].strip()
        
        return json.loads(response)

    def _parse_json_array(self, response: str) -> list:
        """解析 JSON 数组"""
        response = response.strip()
        
        if "```json" in response:
            start = response.find("```json") + 7
            end = response.find("```", start)
            response = response[start:end].strip()
        elif "```" in response:
            start = response.find("```") + 3
            end = response.find("```", start)
            response = response[start:end].strip()
        
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            lines = response.strip().split('\n')
            result = []
            for line in lines:
                line = line.strip().strip('"-').strip("'")
                if line and not line.startswith('[') and not line.startswith(']'):
                    result.append(line)
            return result if result else ["执行任务"]

    async def optimize_plan(self, plan: MissionPlan) -> MissionPlan:
        """优化任务计划"""
        return plan

    async def generate_fallback_plan(
        self, original_plan: MissionPlan, failure_context: Dict[str, Any]
    ) -> MissionPlan:
        """生成降级计划"""
        logger.info(f"Generating fallback plan for {original_plan.mission_id}")
        
        new_strategy_index = min(
            original_plan.current_strategy_index + 1,
            len(original_plan.strategies) - 1
        )
        
        if new_strategy_index >= len(original_plan.strategies) - 1:
            return await self._fallback_decompose(
                original_plan.mission_id,
                UserRequest(content=original_plan.tasks[0].description if original_plan.tasks else "")
            )
        
        new_plan = MissionPlan(
            mission_id=original_plan.mission_id,
            tasks=original_plan.tasks,
            dag=original_plan.dag,
            strategies=original_plan.strategies,
            current_strategy_index=new_strategy_index,
        )
        
        for task in new_plan.tasks:
            task.strategy_index = new_strategy_index
        
        return new_plan

    async def clear_cache(self) -> None:
        """清除缓存"""
        self._decompose_cache.clear()
        logger.info("Planner cache cleared")
