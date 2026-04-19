# OpenAkita 作战指挥室模型重构计划

## 一、概述

本文档详细描述将 OpenAkita 从"分散决策、Agent 自主循环"的架构，改造为"集中指挥、分层执行"的作战指挥室模型的完整计划。

### 1.1 重构目标

| 目标 | 说明 |
|------|------|
| **指挥不干活** | 指挥官只看全局、做决策，不执行具体任务 |
| **干活不指挥** | 军人 Agent 无条件执行分派任务，无调度权限 |
| **遇事上报** | 执行层遇到问题不自行循环重试，而是上报等待指令 |
| **全局可见** | 所有任务状态、进度、异常统一在指挥看板展示 |

---

## 二、当前架构分析

### 2.1 现有核心模块

| 模块 | 位置 | 当前职责 | 重构后去向 |
|------|------|---------|-----------|
| Agent | `core/agent.py` | 主协调类，集成所有子系统 | 改造为 SoldierAgent + 保持兼容 |
| Ralph Loop | `core/ralph.py` | 永不放弃循环，失败重试 | 降级或拆除，逻辑移至 Commander |
| Reasoning Engine | `core/reasoning_engine.py` | ReAct 推理引擎 | 保留为 SoldierAgent 的执行核心 |
| Tool Executor | `core/tool_executor.py` | 工具执行引擎 | 保持不变，由 SoldierAgent 调用 |
| Orchestrator | `agents/orchestrator.py` | 多代理协调器 | 拆分为 Commander + Planner + Dispatcher |
| Agent Factory | `agents/factory.py` | Agent 实例工厂和池管理 | 改造为 SoldierPool |

### 2.2 决策逻辑分布

| 决策类型 | 当前位置 | 目标位置 |
|----------|---------|---------|
| LLM 推理决策 | ReasoningEngine | SoldierAgent (仅战术层面) |
| 任务完成验证 | ReasoningEngine → ResponseHandler | SoldierAgent 上报 + Commander 确认 |
| 回滚决策 | ReasoningEngine | SoldierAgent 上报 + Commander 决策 |
| 委派决策 | Agent → Orchestrator | Commander |
| 重试决策 | Ralph Loop | Commander |
| 超时终止决策 | Orchestrator | Dispatcher 监控 + Commander 决策 |

---

## 三、目标架构设计

### 3.1 角色定义

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

### 3.2 核心角色详细定义

#### 3.2.1 指挥官 (Commander)

**职责：**
- 接收用户请求
- 监听状态汇报
- 做出决策（继续/重派/取消/人工介入）
- 全局资源协调
- 配置策略管理

**禁止事项：**
- 不执行具体任务
- 不调用工具
- 不生成内容

#### 3.2.2 参谋部 (Planner)

**职责：**
- 任务分解（将自然语言任务转为 DAG）
- 路径规划
- 输出 MissionPlan 对象
- 依赖关系管理

**禁止事项：**
- 不派发任务
- 不执行任务

#### 3.2.3 调度台 (Dispatcher)

**职责：**
- 任务队列管理
- 派发任务到军人池
- 超时监控
- 负载均衡
- 军人健康监控

**禁止事项：**
- 不做决策
- 不修改计划

#### 3.2.4 军人 (SoldierAgent)

**职责：**
- 无条件执行分派任务
- 严格按照命令执行
- 上报状态（进度/完成/失败/需澄清）
- 有最大执行步数限制（默认 10 步）

**禁止事项：**
- 不决策
- 不擅自重试
- 不修改任务目标
- 不自主调用其他 Agent
- 不自主切换策略

#### 3.2.5 情报看板 (Dashboard)

**职责：**
- 实时状态展示
- 异常告警
- 人工介入接口（暂停/恢复/取消/重派）
- 历史记录查询

**禁止事项：**
- 不决策

---

## 四、文件结构变更

### 4.1 新增文件

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
    └── soldier.py            # 军人 Agent（从 agent.py 改造）
```

### 4.2 修改文件

| 文件 | 修改内容 |
|------|---------|
| `src/openakita/core/agent.py` | 标记为 deprecated，保留兼容层 |
| `src/openakita/core/ralph.py` | 标记为 deprecated，逻辑移至 Commander |
| `src/openakita/agents/orchestrator.py` | 拆分为 Commander/Planner/Dispatcher |
| `src/openakita/agents/factory.py` | 改造为 SoldierPool |

---

## 五、实施阶段计划

### 阶段一：基础设施搭建（Week 1-2）

**目标：** 建立协议、数据模型和基础框架

**任务清单：**
1. 创建 `protocols/reporting.py` - 统一汇报协议
2. 创建 `scheduler/models.py` - 数据模型
3. 创建 `scheduler/__init__.py` - 模块导出
4. 单元测试覆盖

### 阶段二：核心角色实现（Week 3-4）

**目标：** 实现 Planner、Dispatcher、SoldierAgent

**任务清单：**
1. 实现 `scheduler/planner.py` - 任务分解
2. 实现 `scheduler/dispatcher.py` - 任务派发与监控
3. 实现 `scheduler/soldier_pool.py` - 军人池管理
4. 实现 `agents/soldier.py` - 军人 Agent（改造自 agent.py）
5. 单元测试覆盖 >80%

### 阶段三：指挥官与看板（Week 5-6）

**目标：** 实现 Commander、Dashboard 和集成

**任务清单：**
1. 实现 `scheduler/commander.py` - 全局决策
2. 实现 `scheduler/dashboard.py` - 情报看板
3. 端到端集成测试
4. 向后兼容层实现

### 阶段四：测试与优化（Week 7-8）

**目标：** 完整测试、文档、迁移指南

**任务清单：**
1. 完整测试套件
2. 性能测试与优化
3. 架构文档更新
4. 迁移指南编写
5. 示例代码

---

## 六、验收标准

| 场景 | 期望行为 |
|------|---------|
| 军人执行失败 | 上报指挥官，不自己重试 |
| 任务超时 | 调度台上报，指挥官决策 |
| 多任务并发 | 调度台队列管理，军人池并行执行 |
| 需要人工介入 | 看板告警，指挥官暂停任务等待指令 |
| 任务被取消 | 指挥官发送取消信号，军人立即停止 |
| 长期无人值守 | 指挥官根据汇报自动决策（可配置策略） |

---

## 七、约束条件

1. **向后兼容**：现有 API 接口保持不变
2. **配置化**：指挥官决策策略可配置（自动/人工/混合）
3. **渐进式迁移**：保留旧模块标记 deprecated，给 2 个版本过渡期
4. **测试覆盖**：核心模块（Commander、Dispatcher、Soldier）测试覆盖率 >80%
