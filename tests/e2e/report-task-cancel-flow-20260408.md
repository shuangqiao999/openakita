# 任务终止与状态流转测试报告

**日期**: 2026-04-08  
**环境**: 本地开发（Windows 10，D:\OpenAkita，源码 v1.26.10）  
**后端**: http://127.0.0.1:18900  
**前端**: http://localhost:5173/web/  
**测试目标**: 验证组织项目任务的完整生命周期（创建→派发→执行→终止→状态流转）

---

## 一、测试执行摘要

| 测试项 | 结果 | 严重程度 |
|--------|------|----------|
| 健康检查 | PASS | - |
| 认证检查 | PASS | - |
| 组织/项目/任务 CRUD | PASS | - |
| 任务派发（dispatch） | PASS | - |
| 任务执行中状态流转 | PASS（todo→in_progress→delivered） | - |
| 任务执行中计划与日志更新 | PASS | - |
| **任务终止：PUT status=todo** | **FAIL** | **P0 严重** |
| **任务终止：POST /api/chat/cancel** | **FAIL** | **P0 严重** |
| **前端：in_progress 状态缺少终止按钮** | **FAIL** | **P1 高** |
| **状态一致性：Agent 无视手动状态变更** | **FAIL** | **P0 严重** |

---

## 二、详细测试记录

### 2.1 正常流程测试（PASS）

**测试 1：任务创建与派发**

```
组织: E2E-FullFlow-Test-0408 (org_25989578e2ed)
项目: 任务追踪 (proj_fc8e51ee948b)

1. POST /api/orgs/{org_id}/projects/{project_id}/tasks
   创建任务 task_227aeee19f50，状态 todo → OK

2. POST .../tasks/{task_id}/dispatch
   派发成功，chain_id=dispatch:task_227aeee19f50:60de5c7c → OK
   任务状态立即变为 in_progress → OK
```

**测试 2：正常执行完成**

```
任务 task_227aeee19f50（短任务：输出1-100数字+斐波那契数列）

T+5s:  status=in_progress, progress=0%,  plan_steps=3, logs=2
T+20s: status=in_progress, progress=33%, step_1=completed
T+30s: status=delivered,   progress=100%, 全部3个步骤completed

时间线：
  - PM 节点创建计划（3步骤）
  - step_1: 使用 run_shell 生成数字列表 → completed
  - step_2: 使用 run_shell 生成斐波那契解释 → completed
  - step_3: 整合输出并提交 → completed
  - Agent 通过 org_update_task 工具将状态设为 delivered

结论: 正常任务的完整生命周期（todo→in_progress→delivered）工作正确
```

### 2.2 任务终止测试（FAIL）

**测试 3：通过 PUT API 手动取消 in_progress 任务**

```
任务 task_25a6b85226da（长任务：10步编写爬虫项目，每步500+字）

1. 创建并派发成功
2. 执行过程监控：
   T+5s:  status=in_progress, progress=0%
   T+20s: status=in_progress, progress=33%, step_1完成

3. 在 T+20s 执行取消：
   PUT .../tasks/{task_id}  body={"status": "todo"}
   → HTTP 200, status变为todo

4. 取消后监控：
   T+5s:  status=todo, progress=30%, PM=busy  ← Agent仍在执行！
   T+15s: status=todo, progress=40%, PM=busy  ← 进度继续增长！
   T+25s: status=todo, progress=60%, PM=busy  ← Agent完全无视

结论: PUT 更新状态仅修改了 ProjectStore 中的状态字段，
      但运行中的 Agent asyncio Task 完全不感知，继续执行
```

**测试 4：通过 /api/chat/cancel 终止 org Agent**

```
尝试使用聊天取消机制终止组织节点 Agent：

POST /api/chat/cancel
body: {"conversation_id": "org:org_25989578e2ed:node:pm", "reason": "测试取消"}
→ HTTP 200, {"status": "ok"}

后端日志：
  [Chat API] Cancel 接收到请求
  [StopTask] cancel_current_task 被调用, task_status=N/A
  ⚠️ [StopTask] No task found for session org:...:node:pm, storing as pending cancel
  [Plan] Cancelled plan for session org:...:node:pm
  [StopTask] Task cancellation completed

取消后监控：
  PM节点仍然 busy，Agent继续在 ReAct Iter 17, 18... 执行 run_shell
  ReAct 循环完全没有检测到取消信号

根因分析：
  1. _get_existing_agent() 在 agent_pool 中找不到 org 节点 agent
  2. 回退到全局 agent（主聊天 agent）
  3. 主聊天 agent 的 AgentState 中没有 org session 的任务
  4. 取消信号存入 _pending_cancels，但只在新 chat() 调用时检查
  5. org 节点 agent 在 runtime._agent_cache 中，完全是另一个 Agent 实例
```

**测试 5：前端 UI 检查**

```
OrgProjectBoard.tsx 分析：

in_progress 状态的任务仅显示：
  - "验收" 按钮 (accepted)
  - "打回" 按钮 (rejected)

❌ 没有 "终止/取消" 按钮
❌ 没有 "暂停" 按钮

todo 状态显示 "派发" 按钮
rejected/blocked 状态显示 "重新派发" 按钮

结论: 前端完全缺失 in_progress 任务的终止操作入口
```

---

## 三、问题汇总与根因分析

### 问题 1（P0）：组织任务终止无效 — Agent 无视状态变更

**现象**: 通过 API 将任务状态从 `in_progress` 改回 `todo`，Agent 继续执行

**根因**: 
- `dispatch` API 通过 `asyncio.ensure_future(to_engine(runtime.send_command(...)))` 启动异步任务
- 启动后没有任何反馈通道将 ProjectStore 的状态变更传递给 runtime._running_tasks 中的 asyncio.Task
- ProjectStore（JSON文件存储）和 Agent runtime（内存异步任务）之间完全解耦
- Agent 的 ReAct 循环只检查自己的 `TaskState.cancelled` 标志，不检查 ProjectStore

**影响**: 
- 用户无法终止已派发的任务
- 浪费 LLM 调用（持续消耗 tokens）
- 状态不一致（任务显示 todo 但实际仍在执行，进度继续更新）
- 执行完成后可能覆盖手动设置的状态

### 问题 2（P0）：Chat Cancel API 无法终止 Org Agent

**现象**: `/api/chat/cancel` 返回成功但实际未终止

**根因**: 
- Org 节点 Agent 存储在 `runtime._agent_cache`（按 `org_id:node_id` 索引）
- Chat API 查找 Agent 通过 `agent_pool`（按 conversation_id 索引）
- 两套 Agent 管理体系完全隔离，cancel 信号发送到了错误的 Agent 实例
- 即使 cancel 到达正确的 Agent，ReAct 循环中的取消检查依赖 `TaskState.cancelled`
  但 `_pending_cancels` 只在**新**的 `chat()` 调用时被消费

### 问题 3（P1）：前端缺少任务终止 UI

**现象**: in_progress 任务没有 "终止/取消" 按钮

**根因**: 
- `OrgProjectBoard.tsx` 的 GanttView/KanbanView 仅为 in_progress 任务渲染 "验收"/"打回"
- 没有设计 "取消执行" 的交互流程
- 后端也没有提供对应的 cancel dispatch API

### 问题 4（P1）：任务状态与节点状态不同步

**现象**: 任务状态变为 `todo`，但节点仍为 `busy`

**根因**: 
- 节点状态由 runtime 管理（`_set_node_status`），不受 ProjectStore 影响
- `_activate_and_run_inner` 中节点状态转换：IDLE→BUSY（开始）→IDLE（完成）/ERROR（失败）
- 没有 "外部取消" 的状态路径

### 问题 5（P2）：进度继续更新到已取消的任务

**现象**: 任务状态为 todo 但进度从 30% 增长到 70%

**根因**: 
- Agent 通过 `org_update_task` 工具更新项目任务进度
- `_link_project_task` / `_append_execution_log` 不检查任务当前状态
- 无论任务是什么状态，Agent 都可以写入进度和日志

### 问题 6（P2）：Delivered 状态任务未自动推进

**现象**: 创业公司 task_b74d817a2d48 长期停留在 delivered, progress=90%

**根因**: 
- delivered 需要上级节点（delegated_by）主动验收或打回
- 如果上级节点不在线或未安排检查，任务会一直停留在 delivered
- 缺少超时自动验收或提醒机制

---

## 四、修复计划

### Phase 1：核心任务终止机制（P0，优先级最高）

#### 1.1 新增 Org 任务取消 API

**文件**: `src/openakita/api/routes/orgs.py`

```python
@router.post("/{org_id}/projects/{project_id}/tasks/{task_id}/cancel")
async def cancel_dispatched_task(request, org_id, project_id, task_id):
    """取消正在执行的已派发任务。
    1. 找到任务的 chain_id
    2. 通过 chain_id 找到执行节点
    3. 取消该节点的 running asyncio.Task
    4. 更新 ProjectStore 状态为 todo/cancelled
    5. 重置节点状态为 idle
    """
```

**预估**: 2-3 小时

#### 1.2 Runtime 添加按 chain_id 取消任务的方法

**文件**: `src/openakita/orgs/runtime.py`

```python
async def cancel_node_task(self, org_id: str, node_id: str, 
                           chain_id: str | None = None) -> bool:
    """取消指定节点正在执行的任务。
    1. 从 _running_tasks 找到对应的 asyncio.Task
    2. 调用 task.cancel()
    3. 等待 CancelledError
    4. 重置节点状态为 IDLE
    5. 广播 org:node_status 事件
    """
```

同时需要修改 `_activate_and_run_inner`：
- 捕获 CancelledError 后更新 ProjectTask 状态
- 发出 `org:task_cancelled` 事件

**预估**: 3-4 小时

#### 1.3 Agent cancel 信号传递到 org 节点 Agent

**文件**: `src/openakita/core/agent.py`, `src/openakita/orgs/runtime.py`

方案：在 `cancel_node_task` 中，除了 `asyncio.Task.cancel()` 外，
还直接调用 org 节点 Agent 的 `cancel_current_task()`：

```python
agent = self._agent_cache.get(f"{org_id}:{node_id}")
if agent:
    agent.agent.cancel_current_task(reason, session_id=session_id)
```

这样 ReAct 循环中的 `state.cancelled` 检查就能生效。

**预估**: 1-2 小时

### Phase 2：前端 UI 补全（P1）

#### 2.1 添加 "终止" 按钮

**文件**: `apps/setup-center/src/components/OrgProjectBoard.tsx`

在 GanttView 和 KanbanView 的 `in_progress` 状态操作区添加：

```tsx
{task.status === "in_progress" && (
  <>
    <Button variant="ghost" size="xs" 
      onClick={() => cancelTask(selectedProject.id, task.id)}
      disabled={cancellingTaskId === task.id}>
      终止
    </Button>
    <Button variant="ghost" size="xs" 
      onClick={() => updateTaskStatus(...)}>
      验收
    </Button>
    <Button variant="ghost" size="xs"
      onClick={() => updateTaskStatus(...)}>
      打回
    </Button>
  </>
)}
```

新增 `cancelTask` 函数调用 `POST .../tasks/{task_id}/cancel`。

**预估**: 1-2 小时

#### 2.2 添加实时状态更新

**文件**: `apps/setup-center/src/components/OrgProjectBoard.tsx`

监听 WebSocket 事件 `org:task_cancelled` 并刷新项目列表。

**预估**: 0.5-1 小时

### Phase 3：状态一致性保障（P1-P2）

#### 3.1 Agent 工具更新前检查任务状态

**文件**: `src/openakita/orgs/tool_handler.py`

在 `_link_project_task` 和 `_append_execution_log` 中：
- 检查当前任务状态是否仍为 `in_progress`
- 如果已被取消（status 不是 in_progress），跳过更新或触发自停止

```python
def _link_project_task(self, org_id, chain_id, **kwargs):
    # 检查任务当前状态
    task = store.find_task_by_chain(chain_id)
    if task and task.status not in (TaskStatus.IN_PROGRESS,):
        logger.warning(f"Task {task.id} status is {task.status}, skipping update")
        return
```

**预估**: 1-2 小时

#### 3.2 ProjectStore 状态变更触发 runtime 通知

**文件**: `src/openakita/orgs/project_store.py`

在 `update_task` 中，当状态从 `in_progress` 变为 `todo`/`cancelled` 时，
发出信号通知 runtime 取消对应的执行任务。

方案选择：
- A) 回调机制：ProjectStore 注册 on_status_change 回调
- B) 事件总线：通过 broadcast_event 发出事件，runtime 监听

推荐方案 A，避免引入全局耦合。

**预估**: 2-3 小时

### Phase 4：体验优化（P2）

#### 4.1 Delivered 超时提醒

当任务停留在 delivered 状态超过一定时间（如 30 分钟），
通过 heartbeat 机制提醒上级节点进行验收。

**文件**: `src/openakita/orgs/runtime.py`（`_health_check_loop` 或 `_watchdog_loop`）

**预估**: 2 小时

#### 4.2 任务状态新增 `cancelled` 枚举值

**文件**: `src/openakita/orgs/models.py`

当前 `TaskStatus` 有：`todo, in_progress, delivered, accepted, rejected, blocked`
建议新增：`cancelled`

用于区分"用户主动取消的任务"和"待办任务"。

**预估**: 1 小时（含前端对应修改）

---

## 五、修复优先级排序

| 优先级 | 任务 | 预估 | 依赖 |
|--------|------|------|------|
| 1 | 1.2 Runtime cancel_node_task | 3-4h | 无 |
| 2 | 1.3 Agent cancel 信号传递 | 1-2h | 1.2 |
| 3 | 1.1 新增 cancel API | 2-3h | 1.2 |
| 4 | 2.1 前端终止按钮 | 1-2h | 1.1 |
| 5 | 3.1 工具更新前检查状态 | 1-2h | 无 |
| 6 | 2.2 实时状态更新 | 0.5-1h | 2.1 |
| 7 | 4.2 cancelled 状态枚举 | 1h | 无 |
| 8 | 3.2 状态变更触发通知 | 2-3h | 1.2 |
| 9 | 4.1 Delivered 超时提醒 | 2h | 无 |

**总预估**: 14-20 小时

---

## 六、测试验证清单

完成修复后，需通过以下验证：

- [ ] 派发任务后，点击"终止"按钮，Agent 在 5 秒内停止执行
- [ ] 终止后，节点状态恢复为 idle
- [ ] 终止后，任务状态正确（todo 或 cancelled）
- [ ] 终止后，进度不再更新
- [ ] 终止后，可重新派发同一任务
- [ ] 正常任务完成不受影响（todo→in_progress→delivered→accepted）
- [ ] 并发：多个任务同时执行，取消其中一个不影响其他
- [ ] 前端实时更新：终止后看板/甘特图立即刷新
- [ ] 聊天取消机制不受影响

---

## 七、附录：测试环境数据

### 测试用任务

| 任务 ID | 用途 | 最终状态 | 说明 |
|---------|------|----------|------|
| task_227aeee19f50 | 正常完成测试 | delivered (100%) | 正常流转完成 |
| task_25a6b85226da | 终止测试 | todo (70%) | PM 仍 busy，Agent 仍在执行 |

### 后端日志关键片段

```
[StopTask] cancel_current_task 被调用: reason='测试取消', session_id=org:org_25989578e2ed:node:pm
⚠️ [StopTask] No task found for session org:...:node:pm, storing as pending cancel
[Plan] Cancelled plan for session org:...:node:pm
→ Agent 继续执行 ReAct Iter 17, 18... (run_shell, write_file)
```

### 代码路径参考

| 关键路径 | 文件 |
|----------|------|
| 任务派发 | `src/openakita/api/routes/orgs.py:1896` |
| runtime 执行 | `src/openakita/orgs/runtime.py:627 (_activate_and_run_inner)` |
| Agent 取消 | `src/openakita/core/agent.py:5072 (cancel_current_task)` |
| ReAct 取消检查 | `src/openakita/core/reasoning_engine.py:667` |
| 状态更新工具 | `src/openakita/orgs/tool_handler.py:158 (_link_project_task)` |
| 前端看板 | `apps/setup-center/src/components/OrgProjectBoard.tsx` |
