"""
作战指挥室数据模型测试
"""

import pytest
from datetime import datetime

from openakita.scheduler.models import (
    UserRequest,
    MissionTask,
    MissionPlan,
    Order,
    ExecutionResult,
    MissionStatus,
)
from openakita.protocols.reporting import ReportStatus


class TestMissionStatus:
    """测试任务状态枚举"""

    def test_active_states(self):
        """测试活跃状态"""
        active = MissionStatus.active_states()
        assert MissionStatus.PLANNING in active
        assert MissionStatus.IN_PROGRESS in active
        assert MissionStatus.PAUSED in active

    def test_is_active(self):
        """测试 is_active 属性"""
        assert MissionStatus.IN_PROGRESS.is_active is True
        assert MissionStatus.COMPLETED.is_active is False

    def test_is_terminal(self):
        """测试 is_terminal 属性"""
        assert MissionStatus.COMPLETED.is_terminal is True
        assert MissionStatus.FAILED.is_terminal is True
        assert MissionStatus.IN_PROGRESS.is_terminal is False


class TestUserRequest:
    """测试用户请求"""

    def test_create_request(self):
        """测试创建请求"""
        request = UserRequest(
            request_id="req_123",
            user_id="user_456",
            content="Hello world",
            priority=1,
        )

        assert request.request_id == "req_123"
        assert request.user_id == "user_456"
        assert request.content == "Hello world"
        assert request.priority == 1


class TestMissionPlan:
    """测试任务计划"""

    def test_create_plan(self):
        """测试创建计划"""
        task1 = MissionTask(
            task_id="task_1",
            mission_id="mission_1",
            description="Task 1",
        )
        task2 = MissionTask(
            task_id="task_2",
            mission_id="mission_1",
            description="Task 2",
            dependencies=["task_1"],
        )

        plan = MissionPlan(
            mission_id="mission_1",
            tasks=[task1, task2],
            dag={"task_1": [], "task_2": ["task_1"]},
        )

        assert plan.mission_id == "mission_1"
        assert len(plan.tasks) == 2

    def test_get_ready_tasks(self):
        """测试获取就绪任务"""
        task1 = MissionTask(
            task_id="task_1",
            mission_id="mission_1",
            description="Task 1",
        )
        task2 = MissionTask(
            task_id="task_2",
            mission_id="mission_1",
            description="Task 2",
            dependencies=["task_1"],
        )
        task3 = MissionTask(
            task_id="task_3",
            mission_id="mission_1",
            description="Task 3",
        )

        plan = MissionPlan(
            mission_id="mission_1",
            tasks=[task1, task2, task3],
            dag={"task_1": [], "task_2": ["task_1"], "task_3": []},
        )

        # 初始状态下，task1 和 task3 已就绪
        ready = plan.get_ready_tasks(set())
        assert len(ready) == 2
        task_ids = {t.task_id for t in ready}
        assert "task_1" in task_ids
        assert "task_3" in task_ids

        # task1 完成后，task2 就绪
        ready = plan.get_ready_tasks({"task_1"})
        assert len(ready) == 2
        task_ids = {t.task_id for t in ready}
        assert "task_2" in task_ids
        assert "task_3" in task_ids

    def test_is_complete(self):
        """测试检查完成状态"""
        task1 = MissionTask(
            task_id="task_1",
            mission_id="mission_1",
            description="Task 1",
        )
        task2 = MissionTask(
            task_id="task_2",
            mission_id="mission_1",
            description="Task 2",
        )

        plan = MissionPlan(
            mission_id="mission_1",
            tasks=[task1, task2],
            dag={"task_1": [], "task_2": []},
        )

        assert plan.is_complete(set()) is False
        assert plan.is_complete({"task_1"}) is False
        assert plan.is_complete({"task_1", "task_2"}) is True


class TestExecutionResult:
    """测试执行结果"""

    def test_create_success_result(self):
        """测试创建成功结果"""
        result = ExecutionResult(
            success=True,
            task_id="task_1",
            status=ReportStatus.COMPLETED,
            result="Great!",
            steps_used=5,
            duration_seconds=10.5,
        )

        assert result.success is True
        assert result.status == ReportStatus.COMPLETED
        assert result.result == "Great!"

    def test_create_failure_result(self):
        """测试创建失败结果"""
        result = ExecutionResult(
            success=False,
            task_id="task_1",
            status=ReportStatus.FAILED,
            error="Something went wrong",
        )

        assert result.success is False
        assert result.status == ReportStatus.FAILED
        assert result.error == "Something went wrong"
