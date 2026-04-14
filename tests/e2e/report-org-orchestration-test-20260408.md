# 组织编排功能 E2E 测试报告

**测试日期**: 2026-04-08  
**测试版本**: OpenAkita 1.27.9 (editable mode)  
**后端地址**: http://127.0.0.1:18900  
**前端地址**: http://localhost:5173/  
**测试组织**: `org_e473c8c6715e` (E2E-Orchestration-Test)

---

## 1. 测试环境

### 组织结构

| 节点 ID | 角色 | 部门 | 层级 | max_concurrent_tasks |
|---------|------|------|------|---------------------|
| `node_root` | 项目总监 | 管理层 | 0 (根节点) | 2 |
| `node_dev` | 开发工程师 | 技术部 | 1 | 1 |
| `node_qa` | 测试工程师 | 质量部 | 1 | 1 |

### 连线关系

| 连线 | 类型 |
|------|------|
| node_root → node_dev | hierarchy |
| node_root → node_qa | hierarchy |
| node_dev → node_qa | collaborate |

### 测试指令

```
请制定一个简单的软件测试计划：
1）让开发工程师编写一个Hello World程序的代码概要
2）让测试工程师编写针对该程序的测试用例。
请将任务分别分派给对应的下属节点执行。
```

---

## 2. 测试结果总览

| 测试项 | 结果 | 说明 |
|--------|------|------|
| 组织创建 | ✅ 通过 | 3节点+3边正常创建 |
| 组织启动 | ✅ 通过 | dormant → active 正常 |
| 指令下发(command) | ✅ 通过 | 异步命令提交,返回 command_id |
| 根节点状态变化 | ✅ 通过 | idle → busy → idle 正常 |
| 任务分发(delegate_task) | ✅ 通过 | root 正确调用 org_delegate_task 分别给 dev 和 qa |
| 子节点激活 | ✅ 通过 | dev 和 qa 均从 idle 变为 busy |
| 子节点间协作 | ✅ 通过 | qa 通过 org_send_message 向 dev 咨询代码详情 |
| 进度汇报 | ✅ 通过 | dev 50%, qa 10% 进度汇报均正常 |
| 交付物提交 | ✅ 通过 | dev 和 qa 均通过 org_submit_deliverable 提交结果 |
| Gather 汇总机制 | ✅ 通过 | org:gather_started → gather_complete (2/2) |
| **子节点状态回归idle** | ❌ **失败** | **dev 和 qa 在完成任务后未回到 idle, 持续 busy 超过8分钟** |
| **重复交付** | ❌ **异常** | **dev 重复提交交付物至少5次** |
| **节点自引用消息** | ❌ **异常** | **from_node == to_node 的自引用消息大量出现** |
| **命令最终完成** | ⚠️ 部分通过 | 命令返回了结果, 但子节点仍在循环中 |
| **Token 消耗** | ❌ **异常** | **单条命令触发 29 次 task_completed, 严重浪费** |

---

## 3. 详细时间线

### 3.1 正常阶段 (T+0s ~ T+33s) ✅

```
T+0s   [03:12:36] 命令提交, command_id=02f1e9a57b23
T+1s   [03:12:37] node_root → busy (接收用户指令)
T+12s  [03:12:48] node_root 调用 org_get_org_chart 了解组织结构
T+19s  [03:12:55] node_root 调用 org_delegate_task → node_dev
                   任务: "编写 Hello World 程序的代码概要"
                   chain_id: ...node_roo:node_dev:2a6b3885
T+21s  [03:12:57] node_dev → busy (接收任务)
T+22s  [03:12:58] node_root 调用 org_delegate_task → node_qa
                   任务: "编写 Hello World 程序测试用例"
                   chain_id: ...node_roo:node_qa:94b03d43
T+23s  [03:12:59] node_qa → busy (接收任务)
T+33s  [03:13:09] qa → dev: org_send_message (question) 咨询代码详情
T+33s  [03:13:10] node_root → idle (委派完成)
T+33s  org:gather_started (child_count=2)
```

**分析**: 该阶段完全正常。root 节点智能地：
1. 先查看组织架构 (`org_get_org_chart`)
2. 依次分派任务给 dev 和 qa
3. qa 主动向 dev 发消息请求代码信息（跨节点协作）

### 3.2 子节点工作阶段 (T+33s ~ T+60s) ✅

```
T+38s  node_dev: org_report_progress (50%)
T+41s  node_qa: org_report_progress (10%)
T+48s  node_qa: org_get_node_status(node_dev) — 检查 dev 状态
T+52s  node_dev: org_submit_deliverable → node_root (第1次)
       链路: org:task_delivered, chain_id=...node_dev:2a6b3885
T+57s  node_qa: write_file → test_cases_hello_world.md
T+78s  node_qa: org_submit_deliverable → node_root (第1次)
       链路: org:task_delivered, chain_id=...node_qa:94b03d43
```

**分析**: 该阶段正常。两个子节点独立工作、汇报进度、提交交付物。

### 3.3 Gather 与汇总阶段 (T+60s ~ T+90s) ✅

```
T+82s  node_dev → idle (task_completed)
T+82s  org:task_complete (node_dev)
T+83s  node_qa → idle (task_completed)
T+83s  org:task_complete (node_qa)
T+83s  org:gather_complete (done=2, total=2) ← 汇总完成
T+83s  node_root → busy (开始汇总子节点结果)
T+114s node_root → idle (汇总完成)
```

**分析**: Gather 机制正确运作。Root 被重新激活进行汇总, 然后回到 idle。

### 3.4 ⚠️ 异常反馈循环阶段 (T+82s ~ 永续) ❌

```
T+82s  node_dev: idle → busy
       触发: "[收到任务结果] 来自 node_dev" (自引用消息!)
T+83s  node_qa: idle → busy
       触发: "[收到任务结果] 来自 node_qa" (自引用消息!)
T+88s  node_dev: 再次调用 org_submit_deliverable (第2次)
T+105s node_qa: 再次调用 org_accept_deliverable
T+107s node_dev: idle → busy (又一次自引用触发)
T+107s node_qa: idle → busy (又一次自引用触发)
...循环持续, node_dev 至少提交了5次交付物...
T+480s (8分钟) 组织被手动停止, dev/qa 仍为 busy
```

**最终统计**: `total_tasks_completed: 29` (单条命令应该只需3-4次)

---

## 4. 关键问题分析

### BUG-1: 自引用消息循环 (Critical) 🔴

**现象**: 节点收到 `from_node == to_node` 的消息（自己给自己发消息），导致无限重新激活。

**日志证据**:
```
[OrgRuntime] Node node_qa: busy -> idle (task_completed)
[OrgRuntime] Node node_qa: idle -> busy
  ([收到任务结果] 来自 node_qa [任务链: 2026-04-08T0]: ...)

[OrgRuntime] Node node_dev: busy -> idle (task_completed)
[OrgRuntime] Node node_dev: idle -> busy
  ([收到任务结果] 来自 node_dev [任务链: 2026-04-08T0]: ...)
```

**消息证据** (messages API):
```json
{
  "from_node": "node_dev",
  "to_node": "node_dev",
  "msg_type": "task_result",
  "metadata": {"auto_result": true}
}
```

**根因分析**:

两个可能原因（需进一步确认）：

1. **`_auto_send_result` 的目标计算错误**: `runtime.py:1360-1383` 中 `_auto_send_result` 应发送到 `org.get_parent(node.id)`, 但消息中 `to_node == from_node` 表明 parent 查找可能在某些场景下返回了错误的节点。需检查在 gather/summary 阶段重新激活节点时, org 对象的 edges 是否被正确传递。

2. **Messenger task_affinity 路由劫持**: `messenger.py:266-273` 中 `task_affinity` 机制可能将发往 parent 的消息重定向回了自身：
   ```python
   affinity_node = self._task_affinity.get(chain_id)
   if affinity_node and affinity_node != msg.to_node:
       msg.to_node = affinity_node  # ← 可能改写了目标!
   ```

**涉及代码**:
- `src/openakita/orgs/runtime.py` → `_auto_send_result()` (L1360-1383)
- `src/openakita/orgs/runtime.py` → `_activate_and_run_inner()` (L1174)
- `src/openakita/orgs/messenger.py` → `send()` (L261-310), task_affinity 路由逻辑 (L266-273)
- `src/openakita/orgs/runtime.py` → `_post_task_hook()` (L2346+)

**影响**: 
- 节点无法回到 idle 状态 (用户报告的核心问题)
- Token 持续消耗, 1条命令消耗了 ~100万+ token
- 同一交付物被重复提交 5+ 次

---

### BUG-2: ReasoningEngine ForceToolCall 误判 (Medium) 🟡

**现象**: 节点给出正确的文本回答后, IntentTag 系统判断 "short text with action claims, tool_calls=0" 并强制重试 tool call。

**日志证据**:
```
[ReAct] Iter 1 → FINAL_ANSWER: "交付物已收到，任务已完成，等待项目总监验收。"
[IntentTag] No intent tag, short text with action claims, tool_calls=0
  → ForceToolCall retry (1/1)
[ReAct] Iter 1 → VERIFY: incomplete, continuing loop
```

**根因**: `reasoning_engine.py` 中 IntentTag 逻辑将节点的简短完成回复误判为"声称有动作但没有工具调用", 强制要求使用工具。这在组织节点处理"任务结果确认"这类消息时是不合理的, 因为确认消息只需文本回复。

**涉及代码**: `src/openakita/core/reasoning_engine.py` → IntentTag / ForceToolCall retry 逻辑

**影响**: 
- 增加不必要的 LLM 迭代次数 (qa 节点 11 次迭代才完成)
- 增加 token 消耗
- 延长任务完成时间

---

### BUG-3: Supervisor signature_repeat 频繁触发 (Low) 🟢

**现象**: Supervisor 检测到 `org_list_my_tasks` 和 `org_submit_deliverable` 被重复调用, 触发 NUDGE 剥离工具。

**日志证据**:
```
[Supervisor] Iter 1 → pattern=signature_repeat level=NUDGE:
  Tool 'org_list_my_tasks' called 2 times with varying args
[Supervisor] NUDGE: tools stripped to force text response

[Supervisor] Iter 2 → pattern=signature_repeat level=NUDGE:
  Tool 'org_submit_deliverable' called 2 times with varying args
```

**根因**: 这是 BUG-1 的连锁反应。由于自引用循环, 节点被反复激活, 导致同一工具在短时间内被多次调用。Supervisor 正确检测了异常但无法阻止循环的根源。

---

### BUG-4: max_concurrent_tasks 限制被突破 (Medium) 🟡

**现象**: `node_dev` 配置 `max_concurrent_tasks=1`, 但日志显示其达到了 2 个并发任务。

**日志证据**:
```
[OrgRuntime] Node node_dev already has 2 active tasks,
  message msg_9cec1168ee61 stays in mailbox
```

**根因**: 当节点完成任务后, `_auto_send_result` 和 `_post_task_hook` 几乎同时触发新的消息处理, 导致竞态条件。节点在 `idle → busy` 的瞬间同时收到自引用消息和 mailbox 消息, 超过了并发限制。

**涉及代码**: `src/openakita/orgs/runtime.py` → `_post_task_hook()` 和 `_handle_new_message()`

---

## 5. 正常功能确认

尽管存在上述问题, 以下功能经验证工作正常:

| 功能 | 详情 |
|------|------|
| **组织 CRUD** | 创建/读取/启动/停止 均正常 |
| **层级任务分发** | root 正确通过 hierarchy edge 向下级分派任务 |
| **跨节点通信** | qa → dev 的 question 消息通过 collaborate edge 正确路由 |
| **进度汇报** | org_report_progress 正常记录百分比 |
| **交付物提交** | org_submit_deliverable 正确提交到父节点 |
| **Gather 机制** | 等待所有子链完成, 汇总触发正确 |
| **事件追踪** | progress_events 完整记录了全部交互链路 |
| **Chain ID 追踪** | parent_chain → sub_chain 的链路关系正确维护 |
| **SSE 状态推送** | org:node_status, org:task_complete 等事件正常广播 |
| **组织统计** | /stats API 正确统计节点工作负载、任务数等 |

---

## 6. 修复建议

### 优先级 P0: 修复自引用消息循环

**方案A** (推荐): 在 `_auto_send_result` 中增加自引用检查:
```python
async def _auto_send_result(self, org, node, chain_id, result_text):
    parent = org.get_parent(node.id)
    if not parent or parent.id == node.id:  # ← 增加自引用检查
        return
    ...
```

**方案B**: 在 messenger `send()` 中拦截自引用消息:
```python
async def send(self, msg):
    if msg.from_node == msg.to_node and msg.msg_type == MsgType.TASK_RESULT:
        logger.debug(f"Dropping self-referencing task_result for {msg.from_node}")
        return False
    ...
```

**方案C**: 检查 `task_affinity` 路由逻辑, 确保不会将消息重定向回发送者。

### 优先级 P1: 限制节点在 gather 后的重新激活

在 gather 阶段完成后, 子节点不应再被"任务结果确认"消息重新激活。建议：
- 在 `_gather_children` 完成后清理相关 chain 的所有 pending 消息
- 或在 `_handle_new_message` 中检查消息对应的 chain 是否已经 gather_complete

### 优先级 P2: 优化 IntentTag 对组织节点的判断

组织节点处理"任务结果确认"类消息时, 只需要简短的文本回复。IntentTag 不应强制要求 tool call。建议:
- 对 `org:` 前缀的 session_id 放宽 ForceToolCall 条件
- 或识别消息类型为 `task_result`/`task_accepted` 时降低工具调用要求

---

## 7. 复现步骤

1. 创建组织: 含 1 个根节点 + 至少 2 个子节点, hierarchy 连线
2. 启动组织: `POST /api/orgs/{org_id}/start`
3. 发送命令: `POST /api/orgs/{org_id}/command`, content 要求根节点分派任务给子节点
4. 观察: 子节点完成任务后不回到 idle, 持续在 busy 状态循环
5. 检查 messages API: 可看到大量 `from_node == to_node` 的自引用消息

---

## 8. 附件

- `tests/e2e/_create_test_org.json` — 测试组织创建 payload
- `tests/e2e/_test_command1.json` — 测试命令 payload
- `tests/e2e/_cmd1_final.json` — 命令状态快照 (含完整 progress_events)
- 后端日志: terminal 136889 (openakita serve --dev)
- 事件日志: `data/orgs/org_e473c8c6715e/events/` 目录下 JSONL 文件
