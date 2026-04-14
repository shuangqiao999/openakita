# 组织编排兼容矩阵

## 状态兼容矩阵

| 业务状态 `TaskStatus` | 运行时阶段 `runtime_phase` | 含义 | 前端展示原则 |
| --- | --- | --- | --- |
| `todo` | `null` | 尚未派发 | 展示“待办” |
| `in_progress` | `queued` | 已受理，等待执行 | 展示“排队中” |
| `in_progress` | `running` | 正在执行 | 展示“执行中” |
| `in_progress` | `waiting_children` | 父链等待子链/回复 | 展示“等待子节点” |
| `in_progress` | `gathering` | 汇总子链结果 | 展示“汇总中” |
| `in_progress` | `cancel_requested` | 已发起取消，等待 runtime 收敛 | 展示“取消中” |
| `delivered` | `delivered` | 已交付待验收 | 展示“已交付 / 待验收” |
| `accepted` | `accepted` | 已完成 | 展示“已验收” |
| `rejected` | `rejected` | 已打回 | 展示“已打回” |
| `blocked` | `failed` | 执行失败或派发失败 | 展示“已阻塞 / 失败” |
| `cancelled` | `cancelled` | 已完成取消 | 展示“已取消” |

## 接口兼容矩阵

| 接口 | 现状 | 兼容策略 |
| --- | --- | --- |
| `POST /api/orgs/{org_id}/projects/{project_id}/tasks/{task_id}/dispatch` | 保持原入口 | 补充 `queued` 语义和失败回写 |
| `POST /api/orgs/{org_id}/projects/{project_id}/tasks/{task_id}/cancel` | 新增任务级取消入口 | 内部映射到 `chain_id` |
| `GET /api/orgs/{org_id}/tasks/{task_id}` | 从基础任务详情升级为聚合观察对象 | 保留原任务字段，新增 `runtime / collaboration / child_chains / timeline` |
| `GET /api/orgs/{org_id}/tasks/{task_id}/timeline` | 保留 | 改为统一复用结构化事件与执行日志 |
| `GET /api/orgs/{org_id}/stats` | 保留 | 与任务详情共享事件语义，补充 inbox/任务链观察字段 |
| `GET /api/orgs/{org_id}/inbox` | 保留 | 作为顶部未读/待审批真实计数来源 |

## 字段分层矩阵

### 保留字段

- `TaskStatus`
- `ProjectTask.chain_id`
- `progress_pct`
- `execution_log`
- `plan_steps`
- `assignee_node_id`

### 新增持久字段

- `runtime_phase`
- `current_owner_node_id`
- `waiting_on_nodes`
- `last_error`
- `last_event`
- `cancel_requested_at`
- `cancelled_at`
- `runtime_updated_at`

### 聚合派生字段

- `collaboration.pending_children`
- `collaboration.completed_children`
- `collaboration.failed_children`
- `collaboration.recent_messages`
- `collaboration.latest_message`
- `collaboration.communication_summary`
- `child_chains`
- `timeline`

## 设计约束

- 不新增第二套平行任务状态机。
- `runtime_phase` 只解释过程，不替代业务状态。
- 前端主视图围绕 `task_id + chain_id + current_owner_node_id + waiting_on_nodes + last_event` 收口。
- `blocked` 不再承担“用户取消”的语义。
