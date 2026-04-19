# OpenAkita 作战指挥室架构迁移指南

本文档帮助现有用户从旧架构迁移到新的"作战指挥室"架构。

## 一、概述

### 1.1 为什么迁移

新架构提供以下优势：
- ✅ **更好的可观测性** - 全局看板统一展示所有任务状态
- ✅ **更灵活的决策** - 指挥官的三层策略（换策略/降级/人工）
- ✅ **更清晰的职责** - 指挥不干活，干活不指挥
- ✅ **更易调试** - 状态汇报标准化，问题定位更容易
- ✅ **向后兼容** - 旧 API 保持不变，渐进式迁移

### 1.2 迁移策略

- **渐进式迁移**：旧模块保留并标记为 `deprecated`
- **过渡期**：2 个版本的过渡期
- **并行运行**：新旧架构可以同时运行

---

## 二、架构对比

### 2.1 核心变化

| 组件 | 旧架构 | 新架构 |
|------|--------|--------|
| 主入口 | `Agent` | `Commander` |
| 任务执行 | `Agent` + `RalphLoop` | `SoldierAgent` |
| 任务分解 | `Agent` 内部 | `Planner` |
| 任务派发 | `AgentOrchestrator` | `Dispatcher` |
| 状态展示 | 分散 | `Dashboard` |
| 永不放弃 | `RalphLoop`（机械重试） | `Commander`（策略换路） |

### 2.2 模块映射

| 旧模块 | 新模块 | 状态 |
|--------|--------|------|
| `core/agent.py` | `agents/soldier.py` | 重构 + deprecated |
| `core/ralph.py` | `scheduler/commander.py` | 废弃 + 核心逻辑迁移 |
| `agents/orchestrator.py` | `scheduler/*` | 拆分 |
| `agents/factory.py` | `scheduler/soldier_pool.py` | 重构 |

---

## 三、代码迁移

### 3.1 从 Agent 到 Commander

#### 旧代码

```python
from openakita.core.agent import Agent

agent = Agent()
result = await agent.chat_with_session(
    session_id="session_123",
    user_message="帮我写一个脚本",
)
```

#### 新代码

```python
from openakita.scheduler import (
    Commander,
    Planner,
    Dispatcher,
    SoldierPool,
    UserRequest,
)

# 初始化组件
planner = Planner()
soldier_pool = SoldierPool()
await soldier_pool.initialize()

dispatcher = Dispatcher(soldier_pool)
commander = Commander(planner, dispatcher)

# 启动
await commander.start()

# 提交请求
request = UserRequest(
    request_id="req_001",
    user_id="user_001",
    content="帮我写一个脚本",
    session_id="session_123",
)

mission_id = await commander.receive_request(request)

# 等待完成（示例）
import asyncio
await asyncio.sleep(10)

# 获取结果
mission = await commander.get_mission_status(mission_id)
result = mission.result
```

### 3.2 向后兼容层

我们提供了向后兼容层，旧代码可以继续运行：

```python
# 旧代码无需修改！
from openakita.core.agent import Agent

agent = Agent()
result = await agent.chat_with_session(...)
```

旧的 `Agent` 类内部会使用新的作战指挥室架构，但对外接口保持不变。

### 3.3 从 RalphLoop 到 Commander 策略

#### 旧代码（RalphLoop）

```python
from openakita.core.ralph import RalphLoop

ralph = RalphLoop(max_iterations=100)
result = await ralph.run(my_task_fn)
```

#### 新代码（Commander）

```python
from openakita.scheduler import CommanderConfig, DecisionMode

config = CommanderConfig(
    decision_mode=DecisionMode.FULL_AUTO,
    max_strategy_attempts=5,
    auto_retry_threshold=2,
    allow_degradation=True,
)

commander = Commander(planner, dispatcher, config=config)
```

### 3.4 使用 Dashboard

```python
from openakita.scheduler import Dashboard

dashboard = Dashboard(commander)

# 获取所有任务
missions = dashboard.get_all_missions()

# 获取告警
alerts = dashboard.get_alerts(level="error")

# 人工介入
await dashboard.pause_mission(mission_id)
await dashboard.resume_mission(mission_id)
await dashboard.cancel_mission(mission_id)

# 获取摘要
summary = dashboard.get_summary()
print(f"活跃任务: {summary['status_counts']['in_progress']}")
print(f"告警: {summary['active_alerts']}")
```

---

## 四、配置迁移

### 4.1 新配置项

在 `settings` 中添加以下配置：

```python
# 军人配置
SOLDIER_MAX_STEPS = 10
SOLDIER_TIMEOUT_SECONDS = 300

# 军人池配置
SOLDIER_POOL_MIN_SIZE = 1
SOLDIER_POOL_MAX_SIZE = 10
SOLDIER_POOL_IDLE_TIMEOUT_SECONDS = 1800

# 指挥官配置
COMMANDER_DECISION_MODE = "hybrid"  # full_auto, full_manual, hybrid
COMMANDER_MAX_STRATEGY_ATTEMPTS = 3
COMMANDER_AUTO_RETRY_THRESHOLD = 1
COMMANDER_ALLOW_DEGRADATION = true
COMMANDER_HUMAN_INTERVENTION_TIMEOUT = 3600
COMMANDER_TIMEOUT_ACTION = "continue_auto"
```

### 4.2 配置示例

```python
from openakita.config import settings
from openakita.scheduler import CommanderConfig, DecisionMode

config = CommanderConfig(
    decision_mode=DecisionMode(settings.COMMANDER_DECISION_MODE),
    max_strategy_attempts=settings.COMMANDER_MAX_STRATEGY_ATTEMPTS,
    auto_retry_threshold=settings.COMMANDER_AUTO_RETRY_THRESHOLD,
    allow_degradation=settings.COMMANDER_ALLOW_DEGRADATION,
    human_intervention_timeout_seconds=settings.COMMANDER_HUMAN_INTERVENTION_TIMEOUT,
    timeout_action=settings.COMMANDER_TIMEOUT_ACTION,
)
```

---

## 五、测试迁移

### 5.1 单元测试

旧测试：

```python
def test_agent():
    agent = Agent()
    # ...
```

新测试：

```python
import pytest
from openakita.scheduler import (
    Commander,
    Planner,
    Dispatcher,
    SoldierPool,
    UserRequest,
)

@pytest.mark.asyncio
async def test_commander():
    planner = Planner()
    soldier_pool = SoldierPool()
    await soldier_pool.initialize()

    dispatcher = Dispatcher(soldier_pool)
    commander = Commander(planner, dispatcher)

    await commander.start()

    request = UserRequest(
        request_id="test_001",
        user_id="test_user",
        content="test",
    )

    mission_id = await commander.receive_request(request)
    assert mission_id is not None

    await commander.stop()
    await soldier_pool.shutdown()
```

### 5.2 集成测试

请确保测试以下场景：
- ✅ 军人执行失败时上报
- ✅ 任务超时处理
- ✅ 多任务并发
- ✅ 人工介入
- ✅ 任务取消
- ✅ 指挥官策略切换

---

## 六、常见问题

### 6.1 旧代码还能运行吗？

是的！我们提供了完整的向后兼容层，旧代码无需修改即可运行。旧的 `Agent` 类内部使用新架构，但对外 API 保持不变。

### 6.2 如何逐步迁移？

建议按以下步骤：
1. 先阅读架构文档
2. 在测试环境试用新架构
3. 保留旧代码作为后备
4. 逐步迁移关键功能
5. 监控和验证
6. 完全切换

### 6.3 性能有什么变化？

新架构在多任务场景下性能更好，因为：
- 军人池复用减少初始化开销
- 并行任务调度更高效
- 指挥官的智能策略减少无效重试

### 6.4 如何回滚？

如果需要回滚，只需：
1. 恢复使用旧的 `Agent` 类
2. 移除新架构的初始化代码
3. 旧模块在过渡期内仍然可用

---

## 七、 deprecation 时间表

| 版本 | 状态 | 说明 |
|------|------|------|
| v1.28 | Alpha | 新架构引入，旧模块标记 deprecated |
| v1.29 | Beta | 过渡期，新旧并行 |
| v1.30 | Stable | 旧模块移除（或保留但警告） |

---

## 八、获取帮助

- 架构文档：[BATTLE_ROOM_ARCHITECTURE.md](./BATTLE_ROOM_ARCHITECTURE.md)
- 示例代码：查看 `tests/` 目录
- 问题反馈：GitHub Issues

---

## 九、迁移检查清单

- [ ] 阅读架构文档
- [ ] 阅读迁移指南
- [ ] 在测试环境部署
- [ ] 更新配置
- [ ] 迁移核心功能
- [ ] 运行测试
- [ ] 性能验证
- [ ] 更新文档
- [ ] 准备回滚方案
- [ ] 生产环境部署
