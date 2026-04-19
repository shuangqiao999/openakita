# OpenAkita 作战指挥室架构文档

## 一、概述

本文档描述 OpenAkita 的"作战指挥室"架构模型，这是一个从"分散决策、Agent 自主循环"改造为"集中指挥、分层执行"的架构重构。

### 1.1 核心设计理念

| 理念 | 说明 |
|------|------|
| **指挥不干活** | 指挥官只看全局、做决策，不执行具体任务 |
| **干活不指挥** | 执行层无条件执行分派任务，无调度权限 |
| **遇事上报** | 执行层遇到问题不自行循环重试，而是上报等待指令 |
| **全局可见** | 所有任务状态、进度、异常统一在指挥看板展示 |
| **永不放弃（升华版）** | 不放弃任务目标，但会不断换策略、换路径、降级或请求人工 |

---

## 二、角色定义

### 2.1 五角色模型

```
┌─────────────────────────────────────────────────────────────┐
│                    情报看板 (Dashboard)                      │
│              实时状态展示 · 异常告警 · 人工介入              │
└─────────────────────────────────────────────────────────────┘
                              ↑
                              │ 状态汇报
                              │
┌─────────────────────────────────────────────────────────────┐
│                   指挥官 (Commander)                        │
│  全局决策 · 资源分配 · 异常处理 · 任务取消/重启 · 策略配置  │
└─────────────────────────────────────────────────────────────┘
         ↑                    │                    ↓
         │ 任务请求        │ DAG 计划          │ 派发指令
         │                    │                    │
┌──────────┴────────┐  ┌─────┴──────┐  ┌────────┴──────────┐
│   用户/外部系统    │  │  参谋部    │  │    调度台          │
│                    │  │  (Planner) │  │   (Dispatcher)     │
└───────────────────┘  └────────────┘  └────────┬──────────┘
                                                  │ 任务派发
                                                  │
                                                  ↓
                                    ┌───────────────────────────┐
                                    │      军人池 (SoldierPool)  │
                                    │  ┌─────┐ ┌─────┐ ┌─────┐ │
                                    │  │ S1  │ │ S2  │ │ S3  │ │
                                    │  └─────┘ └─────┘ └─────┘ │
                                    └───────────────────────────┘
```

### 2.2 角色详细职责

#### 2.2.1 指挥官 (Commander)

**文件位置：** `src/openakita/scheduler/commander.py`

**职责：**
- 接收用户请求
- 监听状态汇报
- 做出决策（继续/重派/取消/人工介入）
- 全局资源协调
- 配置策略管理

**三层决策策略：**
1. **自动换策略**：单次失败时自动选择新路径
2. **降级执行**：连续失败达到阈值时切换到简化方案
3. **请求介入**：所有策略尝试失败或高风险操作时暂停并请求人工

**核心价值观（升华自 Ralph Loop）：**
- 永不放弃任务目标，但会不断换策略、换路径、降级或请求人工
- 不放弃的是最终目标，而非机械地重复同一方法

**禁止事项：**
- ❌ 不执行具体任务
- ❌ 不调用工具
- ❌ 不生成内容

**关键方法：**
```python
class Commander:
    async def receive_request(self, request: UserRequest) -> MissionId
    async def cancel_mission(self, mission_id: MissionId) -> None
    async def get_mission_status(self, mission_id: MissionId) -> MissionRecord | None
```

#### 2.2.2 参谋部 (Planner)

**文件位置：** `src/openakita/scheduler/planner.py`

**职责：**
- 任务分解（将自然语言任务转为 DAG）
- 路径规划
- 输出 MissionPlan 对象
- 依赖关系管理

**禁止事项：**
- ❌ 不派发任务
- ❌ 不执行任务

**关键方法：**
```python
class Planner:
    async def decompose_task(self, request: UserRequest) -> MissionPlan
    async def optimize_plan(self, plan: MissionPlan) -> MissionPlan
    async def generate_fallback_plan(self, original_plan: MissionPlan, failure_context: dict) -> MissionPlan
```

#### 2.2.3 调度台 (Dispatcher)

**文件位置：** `src/openakita/scheduler/dispatcher.py`

**职责：**
- 任务队列管理（优先级队列）
- 派发任务到军人池
- 超时监控
- 负载均衡
- 军人健康监控

**禁止事项：**
- ❌ 不做决策
- ❌ 不修改计划

**关键方法：**
```python
class Dispatcher:
    async def dispatch_task(self, task: MissionTask, soldier_id: SoldierId | None = None) -> DispatchResult
    async def cancel_task(self, task_id: str) -> None
    def register_report_callback(self, callback: Callable[[StatusReport], None]) -> None
```

#### 2.2.4 军人 (SoldierAgent)

**文件位置：** `src/openakita/agents/soldier.py`

**职责：**
- 无条件执行分派任务
- 严格按照命令执行
- 上报状态（进度/完成/失败/需澄清）
- 有最大执行步数限制（默认 10 步）

**行为规范（核心约束）：**
- ✅ 只执行分派的任务，不擅自做其他事
- ✅ 不质疑任务合理性
- ✅ 不擅自修改任务目标
- ✅ 有最大执行步数限制
- ✅ 遇到问题 → 上报，不自己循环尝试
- ✅ 没有"永不放弃"，只有"执行/失败/上报/需澄清"

**禁止事项：**
- ❌ 不决策
- ❌ 不擅自重试
- ❌ 不修改任务目标
- ❌ 不自主调用其他 Agent
- ❌ 不自主切换策略

**关键方法：**
```python
class SoldierAgent:
    async def execute(self, order: Order) -> ExecutionResult
    async def cancel(self) -> None
    async def pause(self) -> None
    async def resume(self) -> None
```

#### 2.2.5 情报看板 (Dashboard)

**文件位置：** `src/openakita/scheduler/dashboard.py`

**职责：**
- 实时状态展示
- 异常告警
- 人工介入接口（暂停/恢复/取消/重派）
- 历史记录查询

**禁止事项：**
- ❌ 不决策

**关键方法：**
```python
class Dashboard:
    def get_all_missions(self) -> list[MissionRecord]
    def get_alerts(self, level: str | None = None) -> list[Alert]
    async def pause_mission(self, mission_id: str) -> bool
    async def resume_mission(self, mission_id: str) -> bool
    async def cancel_mission(self, mission_id: str) -> bool
    def get_summary(self) -> dict[str, Any]
```

---

## 三、数据流向

### 3.1 完整数据流

```
用户请求
    ↓
Commander.receive_request()
    ↓
Planner.decompose_task() → MissionPlan (DAG)
    ↓
Commander.approve_plan()
    ↓
Dispatcher.dispatch_batch()
    ↓
    ┌──────────┬──────────┬──────────┐
    ↓          ↓          ↓          ↓
Soldier1  Soldier2   Soldier3   Soldier4
    ↓          ↓          ↓          ↓
execute()  execute()  execute()  execute()
    ↓          ↓          ↓          ↓
    └──────────┴──────────┴──────────┘
               ↓
        StatusReport (stream)
               ↓
Commander.handle_report() → Command (继续/重派/取消)
               ↓
        Dashboard.update()
```

### 3.2 失败处理流程

```
军人执行失败
    ↓
上报 StatusReport (FAILED)
    ↓
Commander._handle_failure()
    ↓
    ┌─────────────────────────────────────┐
    │  三层决策策略                        │
    ├─────────────────────────────────────┤
    │ 1. 同一策略 < 阈值 ? → 重试          │
    │ 2. 还有更多策略 ? → 换策略重试      │
    │ 3. 允许降级 ? → 降级执行             │
    │ 4. 否则 → 请求人工介入               │
    └─────────────────────────────────────┘
```

---

## 四、协议定义

### 4.1 统一汇报协议

**文件位置：** `src/openakita/protocols/reporting.py`

#### 4.1.1 状态码

```python
class ReportStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_CLARIFICATION = "needs_clarification"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    STEPS_EXHAUSTED = "steps_exhausted"
```

#### 4.1.2 状态汇报

```python
@dataclass
class StatusReport:
    mission_id: MissionId
    task_id: TaskId
    soldier_id: SoldierId
    status: ReportStatus
    progress: float  # 0.0 - 1.0
    message: str | None = None
    error: str | None = None
    result: Any = None
    steps_used: int = 0
    max_steps: int = 10
    timestamp: datetime
    metadata: dict[str, Any]
```

#### 4.1.3 指挥官命令

```python
class CommandType(Enum):
    CONTINUE = "continue"
    RETRY = "retry"
    RETRY_WITH_NEW_STRATEGY = "retry_with_new_strategy"
    REDIRECT = "redirect"
    DEGRADE = "degrade"
    CANCEL = "cancel"
    PAUSE = "pause"
    RESUME = "resume"
    CLARIFY = "clarify"
    REQUEST_HUMAN_INTERVENTION = "request_human_intervention"

@dataclass
class Command:
    command_type: CommandType
    mission_id: MissionId
    task_id: TaskId | None = None
    parameters: dict[str, Any]
    timestamp: datetime
```

---

## 五、数据模型

### 5.1 核心模型

**文件位置：** `src/openakita/scheduler/models.py`

#### 5.1.1 用户请求

```python
@dataclass
class UserRequest:
    request_id: str
    user_id: str
    content: str
    context: dict[str, Any]
    priority: int = 0
    created_at: datetime
    session_id: str | None = None
```

#### 5.1.2 任务计划

```python
@dataclass
class MissionTask:
    task_id: TaskId
    mission_id: MissionId
    description: str
    dependencies: list[TaskId]
    priority: int = 0
    max_steps: int = 10
    timeout_seconds: int = 300
    assigned_to: SoldierId | None = None
    retry_count: int = 0
    max_retries: int = 3
    strategy_index: int = 0

@dataclass
class MissionPlan:
    mission_id: MissionId
    tasks: list[MissionTask]
    dag: dict[TaskId, list[TaskId]]  # task_id -> dependencies
    strategies: list[dict[str, Any]]
    current_strategy_index: int = 0
```

#### 5.1.3 执行命令与结果

```python
@dataclass
class Order:
    order_id: str
    task_id: TaskId
    mission_id: MissionId
    description: str
    max_steps: int = 10
    strategy: dict[str, Any]
    parameters: dict[str, Any]

@dataclass
class ExecutionResult:
    success: bool
    task_id: TaskId
    status: ReportStatus
    result: Any = None
    error: str | None = None
    steps_used: int = 0
    duration_seconds: float = 0.0
```

---

## 六、配置化

### 6.1 指挥官配置

```python
@dataclass
class CommanderConfig:
    decision_mode: DecisionMode = DecisionMode.HYBRID
    max_strategy_attempts: int = 3
    auto_retry_threshold: int = 1
    allow_degradation: bool = True
    human_intervention_timeout_seconds: int = 3600
    timeout_action: str = "continue_auto"
```

### 6.2 决策模式

| 模式 | 说明 |
|------|------|
| FULL_AUTO | 全自动模式 - 所有决策自动做出 |
| FULL_MANUAL | 全人工模式 - 所有决策需要人工确认 |
| HYBRID | 混合模式 - 关键决策请求人工，普通决策自动 |

---

## 七、与旧架构的对比

| 对比项 | 当前架构 | 目标架构 |
|--------|---------|---------|
| 决策权 | 分散在主 Agent + Ralph Loop | 集中在指挥官 |
| 执行者自由度 | 高（可决定重试、循环、切换策略） | 低（执行→上报→等待指令） |
| 失败处理 | Agent 自己尝试恢复（永不放弃） | 上报指挥官，由指挥官决策 |
| 任务分解 | 主 Agent 兼任 | 独立的 Planner 模块 |
| 任务派发 | AgentOrchestrator 兼任 | 独立的 Dispatcher |
| 状态管理 | 分散在各 Agent | 统一 Dashboard + 指挥官 |
| 循环控制 | Ralph Loop 无上限 | 军人有步数上限，超时上报 |
| 上报机制 | 不规范/缺失 | 统一状态码 + 标准汇报协议 |
| 永不放弃 | 机械重复同一方法 | 换策略、换路径、降级、请求人工 |

---

## 八、文件结构

```
src/openakita/
├── protocols/
│   ├── __init__.py
│   └── reporting.py          # 统一汇报协议
├── scheduler/
│   ├── __init__.py
│   ├── commander.py          # 指挥官
│   ├── planner.py            # 参谋部
│   ├── dispatcher.py         # 调度台
│   ├── dashboard.py          # 情报看板
│   ├── soldier_pool.py       # 军人池
│   └── models.py             # 数据模型
└── agents/
    └── soldier.py            # 军人 Agent
```

---

## 九、快速开始

### 9.1 基本使用

```python
import asyncio
from openakita.scheduler import (
    Commander,
    Planner,
    Dispatcher,
    SoldierPool,
    Dashboard,
    UserRequest,
)

async def main():
    # 初始化组件
    planner = Planner()
    soldier_pool = SoldierPool()
    await soldier_pool.initialize()

    dispatcher = Dispatcher(soldier_pool)
    commander = Commander(planner, dispatcher)
    dashboard = Dashboard(commander)

    # 启动
    await commander.start()

    # 提交请求
    request = UserRequest(
        request_id="req_001",
        user_id="user_001",
        content="帮我写一个 Python 脚本",
    )

    mission_id = await commander.receive_request(request)
    print(f"Mission created: {mission_id}")

    # 查看状态
    summary = dashboard.get_summary()
    print(f"Summary: {summary}")

    # 等待（实际应用中应该有适当的等待逻辑）
    await asyncio.sleep(10)

    # 关闭
    await commander.stop()
    await soldier_pool.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 十、迁移指南

请参考 [MIGRATION_GUIDE.md](./MIGRATION_GUIDE.md) 获取详细的迁移说明。
