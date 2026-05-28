---
name: cancel-scheduled-task
description: Permanently delete scheduled task. "Turn off"→update_scheduled_task(notify=false), "Pause"→update_scheduled_task(enabled=false)
system: true
handler: scheduled
tool-name: cancel_scheduled_task
category: Scheduled Tasks
---

# Cancel Scheduled Task

【永久删除】定时任务。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| task_id | string | 是 | 任务 ID |

## Important

**操作区分**：
- 用户说"取消/删除任务" → 用此工具
- 用户说"关闭提醒" → 用 `update_scheduled_task` 设 notify=false
- 用户说"暂停任务" → 用 `update_scheduled_task` 设 enabled=false

**注意**：删除后无法恢复！

## Related Skills

- `list-scheduled-tasks`: 获取任务 ID
- `update-scheduled-task`: 修改任务设置

