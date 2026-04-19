"""
作战指挥室端到端集成测试
"""

import asyncio
import pytest

from openakita.scheduler import (
    Commander,
    Planner,
    Dispatcher,
    SoldierPool,
    Dashboard,
    UserRequest,
)


@pytest.mark.asyncio
async def test_basic_workflow():
    """测试基本工作流"""
    # 1. 初始化组件
    planner = Planner()
    soldier_pool = SoldierPool(min_size=1, max_size=2)
    await soldier_pool.initialize()

    dispatcher = Dispatcher(soldier_pool)
    commander = Commander(planner, dispatcher)
    dashboard = Dashboard(commander)

    # 2. 启动
    await commander.start()

    try:
        # 3. 检查初始状态
        summary = dashboard.get_summary()
        assert summary["total_missions"] == 0
        assert summary["active_alerts"] == 0

        # 4. 提交请求
        request = UserRequest(
            request_id="test_e2e_001",
            user_id="test_user",
            content="Test task",
        )

        mission_id = await commander.receive_request(request)
        assert mission_id is not None

        # 5. 等待一小段时间
        await asyncio.sleep(0.5)

        # 6. 检查任务已创建
        summary = dashboard.get_summary()
        assert summary["total_missions"] == 1

        # 7. 获取任务详情
        mission = await commander.get_mission_status(mission_id)
        assert mission is not None
        assert mission.mission_id == mission_id

    finally:
        # 8. 清理
        await commander.stop()
        await soldier_pool.shutdown()


@pytest.mark.asyncio
async def test_dashboard_alerts():
    """测试 Dashboard 告警功能"""
    planner = Planner()
    soldier_pool = SoldierPool(min_size=1, max_size=1)
    await soldier_pool.initialize()

    dispatcher = Dispatcher(soldier_pool)
    commander = Commander(planner, dispatcher)
    dashboard = Dashboard(commander)

    await commander.start()

    try:
        # 注册告警回调
        alerts_received = []

        def on_alert(alert):
            alerts_received.append(alert)

        dashboard.register_alert_callback(on_alert)

        # 获取摘要
        summary = dashboard.get_summary()
        assert "total_missions" in summary
        assert "status_counts" in summary
        assert "active_alerts" in summary

        # 获取所有任务（初始为空）
        missions = dashboard.get_all_missions()
        assert len(missions) == 0

        # 获取告警（初始为空）
        alerts = dashboard.get_alerts()
        assert len(alerts) == 0

    finally:
        await commander.stop()
        await soldier_pool.shutdown()


@pytest.mark.asyncio
async def test_planner_decomposition():
    """测试 Planner 任务分解"""
    planner = Planner()

    request = UserRequest(
        request_id="test_plan_001",
        user_id="test_user",
        content="Write a Python script",
    )

    plan = await planner.decompose_task(request)

    assert plan.mission_id is not None
    assert len(plan.tasks) >= 1
    assert plan.dag is not None


@pytest.mark.asyncio
async def test_soldier_pool_basics():
    """测试 SoldierPool 基本功能"""
    pool = SoldierPool(min_size=1, max_size=3)
    await pool.initialize()

    try:
        # 获取空闲军人
        soldier_id = await pool.get_idle_soldier()
        assert soldier_id is not None

        # 获取军人实例
        soldier = await pool.get_soldier(soldier_id)
        assert soldier is not None
        assert soldier.soldier_id == soldier_id

        # 释放军人
        await pool.release_soldier(soldier_id, success=True)

    finally:
        await pool.shutdown()
