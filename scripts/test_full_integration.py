#!/usr/bin/env python3
"""
OpenAkita 作战指挥室架构 - 全面集成测试脚本
"""
import sys
import asyncio
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))


async def test_protocols():
    """测试协议模块"""
    print("\n" + "=" * 80)
    print("测试 1: 协议模块")
    print("=" * 80)

    from openakita.protocols.reporting import (
        ReportStatus,
        CommandType,
        StatusReport,
        Command,
    )

    print("[OK] 导入成功")

    # 测试状态码
    print(f"[OK] ReportStatus: {len(ReportStatus)} 个状态")
    print(f"[OK] CommandType: {len(CommandType)} 个命令类型")

    # 测试创建 StatusReport
    report = StatusReport(
        mission_id="test_mission_001",
        task_id="test_task_001",
        soldier_id="test_soldier_001",
        status=ReportStatus.COMPLETED,
        progress=1.0,
        result="Test result",
    )
    print(f"[OK] StatusReport 创建成功: {report.mission_id}")

    # 测试创建 Command
    cmd = Command(
        command_type=CommandType.CONTINUE,
        mission_id="test_mission_001",
        task_id="test_task_001",
    )
    print(f"[OK] Command 创建成功: {cmd.command_type}")

    # 测试状态属性
    assert ReportStatus.COMPLETED.is_terminal is True
    assert ReportStatus.IN_PROGRESS.is_terminal is False
    print("[OK] 状态属性测试通过")

    return True


async def test_scheduler_core():
    """测试调度核心模块"""
    print("\n" + "=" * 80)
    print("测试 2: 调度核心模块")
    print("=" * 80)

    from openakita.scheduler.models import (
        UserRequest,
        MissionTask,
        MissionPlan,
        Order,
        ExecutionResult,
        MissionStatus,
    )
    from openakita.scheduler.planner import Planner
    from openakita.scheduler.dispatcher import Dispatcher, DispatchResult
    from openakita.scheduler.soldier_pool import SoldierPool
    from openakita.scheduler.commander import Commander
    from openakita.scheduler.dashboard import Dashboard

    print("[OK] 所有调度模块导入成功")

    # 测试 Planner
    planner = Planner()
    print("[OK] Planner 初始化成功")

    # 测试创建请求
    request = UserRequest(
        request_id="test_req_001",
        user_id="test_user_001",
        content="Test integration task",
    )
    print(f"[OK] UserRequest 创建成功: {request.request_id}")

    # 测试任务分解
    plan = await planner.decompose_task(request)
    print(f"[OK] 任务分解成功: {plan.mission_id}, 任务数: {len(plan.tasks)}")

    # 测试 SoldierPool
    soldier_pool = SoldierPool(min_size=1, max_size=3)
    await soldier_pool.initialize()
    print("[OK] SoldierPool 初始化成功")

    # 测试 Dispatcher
    dispatcher = Dispatcher(soldier_pool)
    print("[OK] Dispatcher 初始化成功")

    # 测试 Commander
    commander = Commander(planner, dispatcher)
    print("[OK] Commander 初始化成功")

    # 测试 Dashboard
    dashboard = Dashboard(commander)
    print("[OK] Dashboard 初始化成功")

    # 测试接收请求
    mission_id = await commander.receive_request(request)
    print(f"[OK] 接收请求成功: mission_id")

    # 测试获取状态
    mission = await commander.get_mission_status(mission_id)
    assert mission is not None
    assert mission.mission_id == mission_id
    print(f"[OK] 获取任务状态成功: {mission.status}")

    # 清理
    await commander.stop()
    await soldier_pool.shutdown()
    print("[OK] 清理成功")

    return True


async def test_soldier_agent():
    """测试军人 Agent"""
    print("\n" + "=" * 80)
    print("测试 3: 军人 Agent")
    print("=" * 80)

    from openakita.agents.soldier import SoldierAgent
    from openakita.scheduler.models import Order, ExecutionResult
    from openakita.protocols.reporting import ReportStatus

    print("[OK] SoldierAgent 导入成功")

    # 创建军人 Agent
    soldier = SoldierAgent(soldier_id="test_soldier_001")
    print(f"[OK] SoldierAgent 创建成功: {soldier.soldier_id}")

    # 创建命令
    order = Order(
        order_id="test_order_001",
        task_id="test_task_001",
        mission_id="test_mission_001",
        description="Test soldier execution",
        max_steps=10,
    )
    print("[OK] Order 创建成功")

    # 测试执行（使用占位实现）
    # 注意：这会使用占位逻辑，不会真正执行
    result = await soldier.execute(order)
    print(f"[OK] 执行完成: success={result.success}, status={result.status}")

    # 测试控制方法
    await soldier.pause()
    print("[OK] pause() 调用成功")

    await soldier.resume()
    print("[OK] resume() 调用成功")

    await soldier.cancel()
    print("[OK] cancel() 调用成功")

    await soldier.shutdown()
    print("[OK] shutdown() 调用成功")

    return True


async def test_enhancements():
    """测试增强模块"""
    print("\n" + "=" * 80)
    print("测试 4: 增强模块")
    print("=" * 80)

    from openakita.enhancements import (
        TrustManager,
        TrustLevel,
        TrustAction,
        ExponentialBackoffRetry,
        retry_with_backoff,
        HealthChecker,
        HealthStatus,
        SnapshotManager,
        StateSnapshot,
    )

    print("[OK] 所有增强模块导入成功")

    # 测试信任系统
    trust_manager = TrustManager()
    print("[OK] TrustManager 初始化成功")

    # 注册任务类型
    trust_manager.register_task_type(
        type_id="test_write_script",
        name="Write Script",
        keywords=["write", "script", "python"],
    )
    print("[OK] 注册任务类型成功")

    # 记录成功动作
    score = trust_manager.record_action(
        "test_write_script", TrustAction.SUCCESS_NO_CORRECTION
    )
    print(f"[OK] 记录成功: score={score.score}, level={score.level}")

    # 测试重试系统
    retryer = ExponentialBackoffRetry()
    print("[OK] ExponentialBackoffRetry 初始化成功")

    # 测试健康检查
    health_checker = HealthChecker()
    print("[OK] HealthChecker 初始化成功")

    # 测试快照系统
    snapshot_manager = SnapshotManager()
    print("[OK] SnapshotManager 初始化成功")

    # 创建快照
    snapshot = snapshot_manager.create_snapshot(
        mission_id="test_mission_001",
        task_id="test_task_001",
        soldier_id="test_soldier_001",
        total_steps=10,
    )
    print(f"[OK] 创建快照成功: {snapshot.snapshot_id}")

    # 更新步骤
    snapshot_manager.update_step(snapshot.snapshot_id, 1, result="Step 1 done")
    print("[OK] 更新步骤成功")

    # 更新上下文
    snapshot_manager.update_context(snapshot.snapshot_id, "test_var", 42)
    print("[OK] 更新上下文成功")

    # 清理
    await snapshot_manager.stop()
    print("[OK] 清理成功")

    return True


async def test_cross_module_integration():
    """测试跨模块集成"""
    print("\n" + "=" * 80)
    print("测试 5: 跨模块集成")
    print("=" * 80)

    from openakita.protocols.reporting import ReportStatus, StatusReport
    from openakita.scheduler import (
        Planner,
        Dispatcher,
        SoldierPool,
        Commander,
        UserRequest,
    )
    from openakita.enhancements import TrustManager, TrustAction

    print("[OK] 跨模块导入成功")

    # 初始化所有组件
    planner = Planner()
    soldier_pool = SoldierPool()
    await soldier_pool.initialize()
    dispatcher = Dispatcher(soldier_pool)
    commander = Commander(planner, dispatcher)
    trust_manager = TrustManager()

    print("[OK] 所有组件初始化成功")

    # 注册任务类型
    trust_manager.register_task_type("integration_test", "Integration Test")

    # 创建请求
    request = UserRequest(
        request_id="cross_test_001",
        user_id="cross_test_user",
        content="Cross module integration test",
    )

    # 提交给指挥官
    mission_id = await commander.receive_request(request)
    print(f"[OK] 跨模块任务提交成功: {mission_id}")

    # 信任度记录
    trust_manager.record_action("integration_test", TrustAction.SUCCESS_NO_CORRECTION)
    print("[OK] 信任度记录成功")

    # 清理
    await commander.stop()
    await soldier_pool.shutdown()
    print("[OK] 跨模块清理成功")

    return True


async def main():
    """主测试函数"""
    print("\n" + "=" * 80)
    print("OpenAkita 作战指挥室架构 - 全面集成测试")
    print("=" * 80)

    results = []

    try:
        results.append(("协议模块", await test_protocols()))
        results.append(("调度核心模块", await test_scheduler_core()))
        results.append(("军人 Agent", await test_soldier_agent()))
        results.append(("增强模块", await test_enhancements()))
        results.append(("跨模块集成", await test_cross_module_integration()))

    except Exception as e:
        print(f"\n[FAIL] 测试过程中发生错误: {e}")
        import traceback

        traceback.print_exc()
        results.append(("整体测试", False))

    # 总结
    print("\n" + "=" * 80)
    print("测试总结")
    print("=" * 80)

    all_passed = True
    for test_name, passed in results:
        status = "[OK]" if passed else "[FAIL]"
        print(f"{status} {test_name}")
        if not passed:
            all_passed = False

    print("\n" + "=" * 80)
    if all_passed:
        print("[OK] 所有测试通过！")
        sys.exit(0)
    else:
        print("[FAIL] 部分测试失败！")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
