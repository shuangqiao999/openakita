# 组织编排重构 Phase 6 验收报告

> 日期: 2026-04-07  
> 范围: 组织编排重构计划剩余阶段收尾 (`阶段 3-6`)  
> 结论: 通过

## 覆盖范围

### 已收口事项

- 顶部收件箱红点改为绑定真实未读/待审批计数
- 组织设置与组织黑板改为同一侧栏内标签切换
- 任务详情补齐结构化通信摘要
- 画布在任务聚焦时展示通信链路高亮与等待回复状态
- 任务派发失败时回写任务阻塞/失败状态
- 心跳防重入检查与 runtime 运行任务键保持一致
- 阶段 0 的兼容矩阵正式落盘

### 分层验证矩阵

| 层级 | 场景 | 结果 |
| --- | --- | --- |
| 单元/集成 | `dispatch -> failure -> blocked/failed` | 通过 |
| 单元/集成 | `task detail -> runtime/collaboration/timeline` | 通过 |
| 单元/集成 | `cancel task route -> runtime.cancel_chain` | 通过 |
| 单元/集成 | `cancel root chain + child chain` | 通过 |
| 前端静态 | `OrgEditorView / OrgProjectBoard / TopBar / InboxSidebar` 类型检查 | 通过 |
| 前端静态 | 最近修改文件 lint 诊断 | 通过 |
| 人工复核 | 收件箱真实计数语义 | 通过 |
| 人工复核 | 设置/黑板单面板标签切换 | 通过 |
| 人工复核 | 通信摘要与画布链路高亮 | 通过 |

## 实际执行命令

```text
npx tsc --noEmit
py -3.11 -m pytest tests/orgs/test_api.py tests/orgs/test_runtime.py
py -3.11 -m py_compile src/openakita/api/routes/orgs.py src/openakita/orgs/runtime.py src/openakita/orgs/heartbeat.py tests/orgs/test_api.py tests/orgs/test_runtime.py
```

## 实际结果

- `npx tsc --noEmit`: 通过
- `pytest tests/orgs/test_api.py tests/orgs/test_runtime.py`: `41 passed`
- `py_compile`: 通过
- 最近修改文件 `ReadLints`: 无诊断

## 关键人工验收项

1. 顶栏收件箱不再依赖 `activityFeed.length`，而是由 `/api/orgs/{org_id}/inbox` 返回的 `unread_count / pending_approvals` 驱动。
2. `组织设置` 与 `组织黑板` 已收口为同一 `PanelShell` 内的标签切换，不再出现双面板并存。
3. `GET /api/orgs/{org_id}/tasks/{task_id}` 现在返回 `communication_summary`，可区分待回复与已回复链路。
4. 项目板选中任务后，画布会同步高亮任务节点与通信边，能直观看到等待回复与协作路径。

## 残余说明

- 当前报告覆盖了计划要求的主链验证与本轮收口修改。
- 更深层的多实例一致性、长时间运行日志性能、全前端自动化用例仍属于后续工程加固议题，不属于本轮计划阻塞项。
