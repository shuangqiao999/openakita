# 组织编排任务调度

## 架构概述

组织（Organization）中的每个节点（OrgNode）通过全局 TaskQueue 提交和执行任务。调度体系分为两层：

1. **组织级并发控制**：每个组织最多同时有 `max_concurrent_nodes_per_org`（默认 5）个节点在执行任务。由 `asyncio.Semaphore` 控制。
2. **节点级并发控制**：每个节点最多同时运行 `max_concurrent_per_node`（默认 2）个任务。由 TaskQueue 的 per-node `asyncio.Semaphore` 控制。

```
send_command / org_delegate_task
    │
    ▼
_activate_and_run(org, node, prompt)
    │  获取 org_semaphore (max_concurrent_nodes_per_org=5)
    │
    ▼
TaskQueue.enqueue_task(factory, org_id, node_id, max_concurrent=2)
    │  获取 node_semaphore (max_concurrent_per_node=2)
    │
    ▼
_activate_and_run_inner(org, node, prompt, chain_id)
    │  设置节点状态为 BUSY
    │  执行 _run_agent_task
    │  后处理 _post_task_hook
    │  设置节点状态为 IDLE
    └─► 返回结果
```

## 任务提交

### 通过工具调用

LLM 在对话中调用 `org_delegate_task` 工具即可提交任务。该工具内部调用 `OrgRuntime.send_command()` → `_activate_and_run()`。

```json
{
  "tool": "org_delegate_task",
  "parameters": {
    "org_id": "my-org",
    "node_id": "ceo",
    "message": "分析上周销售数据并生成报告"
  }
}
```

### 通过 Python API

```python
runtime = get_org_runtime()
result = await runtime.send_command(
    org_id="my-org",
    node_id="analyst",
    user_id="admin",
    message="分析上周销售数据",
)
print(result)  # {"ok": True, "text": "报告已生成...", ...}
```

## 并发控制

### 节点并发限制

每个节点的 `max_concurrent_per_node` 默认为 2。当一个节点已有 2 个活跃任务时，后续提交的任务会在 TaskQueue 内部排队，等待信号量释放后自动执行。

### 组织并发限制

每个组织的 `max_concurrent_nodes_per_org` 默认为 5。超过 5 个节点同时执行时，新节点需等待。

### 排队机制

- 任务在 `enqueue_task` 调用时立即返回一个 `asyncio.Future`
- 调用方 `await future` 会等待任务真正开始执行并完成
- 如果节点信号量已满，任务在 `_runner` 协程内部 `await sem.acquire()` 处排队

## 取消语义

### 取消单个节点任务

```python
await runtime.cancel_node_task(org_id, node_id, reason="用户取消")
```

效果：
1. 调用 `agent.cancel_current_task()` 中断 ReAct 循环
2. 调用 `TaskQueue.cancel_node_tasks()` 取消该节点下**所有**任务：
   - 取消正在运行的 `asyncio.Task`（`CancelledError` 传播到执行体）
   - 取消所有等待中的 `asyncio.Future`
3. 任务执行体的 `finally` 块将节点状态重置为 `IDLE`
4. 信号量自动释放

### 取消组织所有任务

```python
await runtime.stop_org(org_id)
```

效果：
1. `_cancel_busy_nodes()` — 逐个取消所有 BUSY 节点的任务
2. `TaskQueue.cancel_org_tasks()` — 取消该组织下所有排队及运行中任务
3. 所有节点状态重置为 `IDLE`

### 取消排队中（尚未执行）的任务

当调用 `cancel_node_tasks` 时，TaskQueue 会同时：
- 取消 `_registered` 中正在运行的 `asyncio.Task`
- 取消 `_node_futures` 中等待信号量的 `asyncio.Future`

被取消的 Future 会抛出 `CancelledError`，调用方需妥善处理。

## 工作窃取

### 堆队列路径

传统的 `enqueue` 路径（payload→handler 模式）支持跨 TaskQueue 实例的工作窃取。通过 `add_steal_target()` 注册其他 TaskQueue，当本地队列为空时自动从目标队列窃取任务。

### enqueue_task 路径（组织任务）

`enqueue_task` 使用 per-node 信号量模型，不依赖工作窃取。原因：
- 每个任务绑定到特定的 `(org_id, node_id)` 组合
- per-node 信号量提供精确的并发控制
- asyncio 事件循环自然提供公平调度（所有 `_runner` 协程竞争执行时间）

`enable_work_stealing` 配置（默认 `True`）仅控制堆队列路径的窃取行为。

## 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_concurrent_per_node` | 2 | 每个节点最多同时运行的任务数 |
| `max_concurrent_nodes_per_org` | 5 | 每个组织最多同时运行的节点数 |
| `delegate_max_parallel` | 5 | `delegate_parallel` 最大并行子任务数 |
| `agent_state_ttl` | 30 | 子 Agent 状态保留时间（秒） |
| `task_queue_cleanup_interval` | 60 | 任务队列内部清理间隔（秒） |
| `enable_work_stealing` | `True` | 是否启用跨队列工作窃取（堆队列路径） |
| `enable_adaptive_concurrency` | `True` | 是否启用自适应并发控制 |

## 状态监控

### 查看任务队列状态

```python
from openakita.main import _orchestrator
stats = _orchestrator._task_queue.get_stats()
print(stats)
# {
#   "pending": 0,
#   "blocked_by_deps": 0,
#   "active": 2,
#   "registered": 2,
#   "total_enqueued": 10,
#   "total_completed": 8,
#   "max_concurrent": 8,
# }
```

### 查看节点活跃任务数

```python
count = await task_queue.get_node_active_count(org_id, node_id)
```

## 故障排查

### 任务一直在排队不执行

1. 检查节点的 `max_concurrent_per_node` 和当前活跃任务数
2. 确认节点状态不是 `FROZEN` 或 `OFFLINE`
3. 查看 TaskQueue stats 中的 `registered` 计数

### 取消任务后节点未恢复 IDLE

`cancel_node_task` 在取消 asyncio Task 后，任务的 `finally` 块会自动将节点状态设为 `IDLE`。如果状态未恢复，检查：
- 任务的 `_activate_and_run_inner` 是否抛出了未捕获的异常
- org 的 `_suppress_post_hook` 是否被设置

### 子任务未被级联取消

当前取消操作（`cancel_node_task`）不会自动级联取消子任务。如需级联取消，需：
1. 通过 `chain_id` 关联父子任务
2. 在取消父任务后，手动遍历所有节点查找同一 chain 的任务并取消
