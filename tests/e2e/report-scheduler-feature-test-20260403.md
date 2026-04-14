# OpenAkita 调度器功能专项测试报告 (2026-04-03)

- **测试范围**: Arch1 / Feat5 / Feat8a-e / Feat8-i18n / Bug1-2
- **后端**: `http://127.0.0.1:18900` — `openakita.exe serve --dev` (v1.27.7, PID 21368)
- **前端**: `http://localhost:5173/web/`
- **驱动脚本**: `tests/e2e/_scheduler_test_v1.py`
- **运行日志**: `tests/e2e/_scheduler_test_v1.run.log`
- **总 API 调用**: 28 次，**0 错误**
- **总 Chat 调用**: 3 次，**0 错误**
- **总耗时**: 100.4s

---

## 总览

| 测试项 | 编号 | 后端 API | 对话侧 | 代码审查 | 前端 UI | 结论 |
|--------|------|----------|--------|----------|---------|------|
| 执行记录存储升级 | Arch1 | PASS | — | PASS | — | **通过** |
| 执行历史查看功能 | Feat5 | PASS | 部分通过 | PASS | 需手动 | **通过** (有发现) |
| 国际化文案补全 | Feat8-i18n | — | — | PASS | 需手动 | **通过** (有发现) |
| "已错过"任务状态 | Feat8a | PASS | — | PASS | 需手动 | **通过** |
| 自动禁用警告 | Feat8b | PASS | — | PASS | 需手动 | **通过** |
| 启用/禁用反馈 | Feat8c | PASS | — | PASS | 需手动 | **通过** |
| 加载失败提示 | Feat8d | PASS | — | PASS | 需手动 | **通过** |
| 名称校验文案 | Feat8e | PASS | — | PASS | 需手动 | **通过** (有发现) |
| Bug1 safe_write 导入 | Bug1 | — | — | PASS | — | **已修复** |
| Bug2 useCallback 优化 | Bug2 | — | — | PASS | — | **已修复** |

---

## 一、Arch1 — 执行记录存储方式升级

### 测试方法
1. 创建 interval 类型 reminder 任务
2. 触发 2 次执行
3. 验证执行记录可查询且递增
4. 检查磁盘文件格式

### 测试结果

| 检查点 | 结果 | 详情 |
|--------|------|------|
| 创建任务 | PASS | `task_04f242dabab1`, status=ok |
| 第 1 次触发 | PASS | exec_93f2cae9ded3, status=success, 2.13s |
| 查询执行记录 | PASS | total=1, 所有字段(id/task_id/started_at/finished_at/status/duration_seconds)齐全 |
| 第 2 次触发 | PASS | exec_0b1e42be73d9, status=success, 1.82s |
| 2 次后记录数 | PASS | total=2 (追加不重写) |
| 磁盘文件格式 | **PASS** | `data/scheduler/executions.json` = **JSONL 格式**，408 行，首字符 `{` |
| 行数 < 2000 | PASS | 408 行，未触发裁剪 |

### 代码审查

| 代码路径 | 检查项 | 结果 |
|-----------|--------|------|
| `scheduler.py:610-647` | `_load_executions`: 支持旧 JSON 数组 + 新 JSONL | PASS |
| `scheduler.py:649-658` | `_migrate_to_jsonl`: 旧格式一次性迁移为 JSONL | PASS |
| `scheduler.py:672-678` | `_append_execution`: 使用 `append_jsonl()` 追加单行 | PASS |
| `scheduler.py:681-696` | `_trim_executions_file`: > 2000 行时保留最近 1000 行 | PASS |
| `scheduler.py:105` | `start()` 调用 `_trim_executions_file()` | PASS |

---

## 二、Feat5 — 执行历史查看功能

### 后端 API 测试

| 接口 | 测试 | 结果 |
|------|------|------|
| `GET /api/scheduler/executions?limit=3&offset=0` | 全局分页 第 1 页 | PASS (3 条) |
| `GET /api/scheduler/executions?limit=3&offset=3` | 全局分页 第 2 页 | PASS (3 条，与第 1 页无重叠) |
| `GET /api/scheduler/tasks/{id}/executions?limit=10` | 按任务过滤 | PASS (2 条，全部 task_id 匹配) |

**返回数据字段完整性**:

```json
{
  "id": "exec_93f2cae9ded3",
  "task_id": "task_04f242dabab1",
  "started_at": "2026-04-03T14:38:01.742720",
  "finished_at": "2026-04-03T14:38:03.877186",
  "status": "success",
  "result": "Arch1 test reminder",
  "error": null,
  "duration_seconds": 2.134466
}
```

所有 7 个字段均存在且类型正确。

### 对话侧测试

| 测试 | 工具 | 结果 |
|------|------|------|
| "查看定时任务列表" | `list_scheduled_tasks` | PASS — 正确列出 16 个任务 |
| "查看任务 {id} 的执行历史" | `search_conversation_traces`, `list_recent_tasks` | **未调用 `query_task_executions`** |
| "定时任务最近执行得怎么样？" | `list_recent_tasks`, `search_conversation_traces` x2 | PASS — 给出概况 |

> **发现 F1**: 当直接要求查看某个任务的执行历史时，AI 没有调用 `query_task_executions` 工具，而是使用了 `search_conversation_traces` 和 `list_recent_tasks`。这可能是因为 `query_task_executions` 在工具分类注册中缺失（`CATEGORY_PREFIXES["Scheduled"]` 未包含它），或者 LLM 对该工具的描述不够熟悉。功能本身不影响，但减少了用户通过对话查询执行历史的精确度。

### 前端 UI (代码审查)

| 组件 | 位置 | 验证 |
|------|------|------|
| History 按钮 | `SchedulerView.tsx:1087-1095` | `<History>` 图标按钮，`onClick -> toggleHistory(task.id)` |
| 展开面板 | `SchedulerView.tsx:1194-1242` | 显示 executionHistory 标题 + 执行列表 |
| toggleHistory | `SchedulerView.tsx:355-367` | `useCallback` + `expandedHistoryRef` pattern |
| API 调用 | 面板内 | `GET /api/scheduler/tasks/${taskId}/executions?limit=10` |
| 空状态 | `SchedulerView.tsx:1204` | `t("scheduler.noExecutions")` |

---

## 三、Feat8-i18n — 国际化文案补全

**所有 16 个新增 key 在 zh.json 和 en.json 中均存在**:

| Key | 中文 | 英文 | 代码引用 |
|-----|------|------|----------|
| `statusMissed` | 已错过 | Missed | `SchedulerView.tsx:580,607` |
| `missedHint` | 该任务在离线期间错过了计划执行时间 | This task missed its scheduled... | **⚠️ 未引用** |
| `autoDisabledWarning` | 该任务因连续失败已被自动禁用... | This task was auto-disabled... | `SchedulerView.tsx:1178` |
| `failWarning` | 已连续失败 {{count}} 次 | Failed {{count}} times in a row | `SchedulerView.tsx:1159` |
| `enableSuccess` | 任务已启用 | Task enabled | `SchedulerView.tsx:521` |
| `disableSuccess` | 任务已暂停 | Task paused | `SchedulerView.tsx:521` |
| `loadError` | 加载任务列表失败，请稍后重试 | Failed to load task list... | `SchedulerView.tsx:377` |
| `loadChannelError` | 加载通道列表失败 | Failed to load channel list | `SchedulerView.tsx:389` |
| `nameRequired` | 请输入任务名称 | Please enter a task name | `SchedulerView.tsx:432` |
| `executionHistory` | 执行历史 | Execution History | `SchedulerView.tsx:1202` |
| `noExecutions` | 暂无执行记录 | No execution records | `SchedulerView.tsx:1204` |
| `duration` | 耗时 | Duration | `SchedulerView.tsx` (exec panel) |
| `executionSuccess` | 成功 | Success | `SchedulerView.tsx` (exec status) |
| `executionFailed` | 失败 | Failed | `SchedulerView.tsx` (exec status) |
| `viewHistory` | 查看历史 | View History | `SchedulerView.tsx:1091` |
| `hideHistory` | 收起历史 | Hide History | `SchedulerView.tsx:1091` |

> **发现 F2**: `scheduler.missedHint` 在 i18n 文件中已定义，但在 `SchedulerView.tsx` 中**从未被 `t()` 调用引用**。目前 missed 状态的 tooltip 使用的是 `statusDotTip()` 函数（返回 `t("scheduler.statusMissed")` = "已错过"），但没有使用更详细的 `missedHint`（"该任务在离线期间错过了计划执行时间"）。这是一个**死文案 key**，建议在 missed 状态的 tooltip 中使用 `missedHint` 替代简短的 `statusMissed`，或者移除该 key。

---

## 四、Feat8a — "已错过"任务状态

### 后端验证

| 检查点 | 结果 |
|--------|------|
| `TaskStatus.MISSED = "missed"` | PASS (`task.py:42`) |
| 状态转换表包含 MISSED | PASS (`task.py:298,307`) |
| `_recalculate_missed_run` 对一次性任务设置 MISSED | PASS (`scheduler.py:517-522`) |
| `start()` 中检测 `next_run < now` 的任务 | PASS (`scheduler.py:111-120`) |
| `metadata["missed_at"]` 记录错过时间 | PASS (`scheduler.py:521`) |

### API 创建过去时间任务测试

创建 `run_at = 2 小时前` 的一次性任务后状态为 `running`（而非 `missed`）。这是**预期行为**：
- `missed` 检测只在 `start()` 时对**已持久化的任务**执行
- 新创建的过去时间任务会被 `add_task -> trigger.get_next_run_time()` 计算并由调度循环立即执行
- 重启后若该任务仍未执行，才会标为 `missed`

### 前端验证 (代码审查)

| 检查点 | 结果 | 位置 |
|--------|------|------|
| 黄色圆点 `DotYellow` | PASS | `SchedulerView.tsx:570` |
| missed 在 ACTIVE_STATUSES 中 | PASS | `SchedulerView.tsx:334` |
| 底部图例包含黄色 | PASS | `SchedulerView.tsx:1256` |
| Tooltip 显示 "已错过" | PASS | `statusDotTip():580` |

---

## 五、Feat8b — 自动禁用警告

### 后端验证

| 检查点 | 结果 |
|--------|------|
| `fail_count` 字段存在于任务数据 | PASS (API 返回 `fail_count: 0`) |
| `metadata` 字段存在 | PASS |
| 连续失败 >= 5 次自动禁用 | PASS (`task.py:402-405`) |
| 失败时记录 `metadata["last_error"]` | PASS (`task.py:398-400`) |

### 前端验证 (代码审查)

| 检查点 | 结果 | 位置 |
|--------|------|------|
| `fail_count > 0` 显示 AlertTriangle 图标 | PASS | `SchedulerView.tsx:1148-1164` |
| `fail_count >= 3` 使用 `failWarning` 文案 | PASS | `SchedulerView.tsx:1158-1159` |
| 自动禁用横幅 (`status=failed && !enabled`) | PASS | `SchedulerView.tsx:1168-1180` |
| 横幅使用 `autoDisabledWarning` 文案 | PASS | `SchedulerView.tsx:1178` |

---

## 六、Feat8c — 启用/禁用操作反馈

### 后端验证

| 操作 | 响应 | 状态变化 |
|------|------|----------|
| Toggle (禁用) | `{status: "ok", task: {..., enabled: false}}` | PASS |
| Toggle (启用) | `{status: "ok", task: {..., enabled: true}}` | PASS |

API 返回包含完整的 `task` 对象，前端可据此刷新 UI 并显示 toast。

### 前端验证 (代码审查)

| 检查点 | 结果 | 位置 |
|--------|------|------|
| Toggle 成功后 `toast.success()` | PASS | `SchedulerView.tsx:521` |
| Toggle 失败后 `toast.error()` | PASS | `showMsg(msg, false) -> toast.error()` |
| 使用 `sonner` 库 | PASS | `import { toast } from "sonner"` |

---

## 七、Feat8d — 加载失败提示

### 后端验证

| 场景 | 响应 |
|------|------|
| `GET /api/scheduler/tasks/nonexistent_id` | `{error: "Task not found"}` (HTTP 200) |
| `GET /api/scheduler/tasks/nonexistent_id/executions` | `{executions: [], total: 0}` (HTTP 200) |
| `POST /api/scheduler/tasks/nonexistent_id/toggle` | `{error: "Task not found"}` (HTTP 200) |
| `DELETE /api/scheduler/tasks/nonexistent_id` | `{error: "Task not found"}` (HTTP 200) |

> **发现 F3**: 所有错误响应的 HTTP status code 都是 **200**（而非 404/400）。错误信息通过 JSON body 中的 `error` 字段传递。这是一致的设计模式（非 RESTful 标准），前端通过检查 `response.error` 来判断失败。不影响功能，但属于 API 设计风格选择。

### 前端验证 (代码审查)

| 检查点 | 结果 | 位置 |
|--------|------|------|
| `fetchTasks` 失败时 `toast.error(loadError)` | PASS | `SchedulerView.tsx:377` |
| `fetchChannels` 失败时 `toast.error(loadChannelError)` | PASS | `SchedulerView.tsx:389` |

---

## 八、Feat8e — 名称校验

### 后端验证

| 测试 | 结果 |
|------|------|
| 空字符串 `""` 创建任务 | 后端 **接受** (200, task_id 返回) |
| 纯空格 `"   "` 创建任务 | 后端 **接受** (200, task_id 返回) |

> **发现 F4**: 后端 API 不做名称校验。`TaskCreateRequest` 的 `name: str` 没有 `min_length` 约束。名称校验完全依赖前端 `saveTask` 中的 `if (!form.name.trim())` 检查。如果通过 API 直接调用（如脚本、curl），可以创建空名任务。建议在后端也增加 `name` 非空校验。

### 前端验证 (代码审查)

| 检查点 | 结果 | 位置 |
|--------|------|------|
| `form.name.trim()` 校验 | PASS | `SchedulerView.tsx:432` |
| 使用 `nameRequired` 文案 | PASS | `"请输入任务名称"` (非旧版 "输入任务名称") |
| 提交时 `trim()` | PASS | `payload.name: form.name.trim()` |

---

## 九、Bug1 — safe_write 导入

| 检查点 | 文件 | 行号 | 结果 |
|--------|------|------|------|
| 模块顶部导入 `safe_write` | `scheduler/scheduler.py` | 19 | `from ..utils.atomic_io import safe_json_write, safe_write` PASS |
| `_migrate_to_jsonl` 使用 `safe_write` | 同上 | 656 | PASS |
| `_trim_executions_file` 使用 `safe_write` | 同上 | 692 | PASS |
| `_append_execution` 使用 `append_jsonl` | 同上 | 674 | PASS (from `..utils.atomic_io`) |
| `safe_write` 定义存在 | `utils/atomic_io.py` | 52-95 | PASS |

**结论**: Bug1 已修复，`safe_write` 在文件头部正确导入，不会出现 `NameError`。

---

## 十、Bug2 — useCallback + ref 优化

| 检查点 | 位置 | 结果 |
|--------|------|------|
| `expandedHistory` state | `SchedulerView.tsx:351` | `useState<Record<string, TaskExecution[]>>({})` |
| `expandedHistoryRef` ref | `SchedulerView.tsx:352` | `React.useRef(expandedHistory)` |
| ref 同步 | `SchedulerView.tsx:353` | `expandedHistoryRef.current = expandedHistory` (每次 render 更新) |
| `toggleHistory` 用 `useCallback` | `SchedulerView.tsx:355` | `useCallback(async (taskId) => ...)` |
| 使用 ref 而非直接闭包 | `SchedulerView.tsx:356` | `expandedHistoryRef.current[taskId]` (避免 stale closure) |

**结论**: Bug2 已修复。`toggleHistory` 使用 `useCallback` 包裹，避免每次 render 重建函数引用。通过 `useRef` 访问最新 `expandedHistory` 值，解决了 stale closure 问题。

---

## 发现问题汇总

| ID | 严重度 | 描述 | 影响 | 建议 |
|----|--------|------|------|------|
| F1 | 低 | `query_task_executions` 工具在对话中未被 LLM 选用 | 用户通过对话查询执行历史时精度不足 | 检查 `CATEGORY_PREFIXES["Scheduled"]` 是否缺少该工具；优化工具 description |
| F2 | 低 | `scheduler.missedHint` i18n key 已定义但未被代码引用 | 死文案 key，missed tooltip 只显示简短的"已错过"而非详细说明 | 在 `statusDotTip` 中对 missed 使用 `missedHint` |
| F3 | 信息 | 所有错误响应使用 HTTP 200 + `{error: "..."}` | 非 RESTful 标准，但前端已适配 | 可选：后续版本考虑使用 HTTP 404/400 |
| F4 | 低 | 后端 API 不校验任务名称非空 | API 直接调用可创建空名任务 | 在 `TaskCreateRequest` 中增加 `name: str = Field(min_length=1)` |

---

## 无法自动化测试的部分（需手动验证）

以下功能涉及前端 UI 视觉效果和交互行为，需要在浏览器中手动操作：

### 1. Feat8a — "已错过"黄色圆点

**操作步骤**:
1. 打开 `http://localhost:5173/web/`，切到"计划任务"页面
2. 查看任务列表中是否有状态为 `missed` 的任务（需要在服务停机期间有过期的一次性任务）
3. **观察**: 该任务左侧应显示**黄色圆点**
4. **鼠标悬停**: 应显示 tooltip "已错过"
5. **切到"进行中"标签**: 该任务应在此标签页中可见
6. **页面底部**: 图例区应包含黄色圆点 + "已错过"

**预期效果**: 黄色圆点、tooltip、标签页过滤、底部图例均正确显示

### 2. Feat8b — 自动禁用警告横幅

**操作步骤**:
1. 如果没有连续失败的任务，可以人工制造：创建一个 Agent 任务（task 类型），使用无效 prompt 使其反复失败
2. 当 `fail_count >= 3` 时，查看任务卡片上是否出现**黄色三角警告图标**
3. 鼠标悬停应显示 "已连续失败 X 次"
4. 当 `fail_count >= 5` 时任务被自动禁用，查看是否出现**黄色警告横幅**，文案为 "该任务因连续失败已被自动禁用，请检查任务配置后重新启用"

**预期效果**: 警告图标 + tooltip + 自动禁用横幅

### 3. Feat8c — 启用/禁用 Toast 通知

**操作步骤**:
1. 找到任意已启用的任务，点击暂停按钮（电源关闭图标）
2. **观察**: 应弹出绿色 toast "任务已暂停"
3. 再次点击启用按钮（电源开启图标）
4. **观察**: 应弹出绿色 toast "任务已启用"

**预期效果**: 操作后立即出现 sonner toast

### 4. Feat8d — 加载失败 Toast

**操作步骤**:
1. 停止后端服务 (`Ctrl+C`)
2. 刷新"计划任务"页面
3. **观察**: 应弹出红色 toast "加载任务列表失败，请稍后重试"
4. 重启后端

**预期效果**: 红色错误 toast + 页面不会白屏

### 5. Feat8e — 名称校验提示

**操作步骤**:
1. 点击"新建任务"按钮
2. 不填任务名称，直接点"保存"
3. **观察**: 应弹出提示 "请输入任务名称"（而非旧版的 "输入任务名称"）

**预期效果**: 正确的校验文案

### 6. Feat8-i18n — 英文环境

**操作步骤**:
1. 将浏览器语言切换为英文（或在设置中切换语言）
2. 检查以上所有功能的英文文案是否正确显示

**预期效果**: 所有新增 key 的英文翻译正确显示

### 7. Feat5 — 执行历史面板

**操作步骤**:
1. 找到有执行记录的任务（如系统心跳任务或刚触发过的任务）
2. 点击任务卡片上的时钟图标（History 按钮）
3. **观察**: 下方展开"执行历史"面板
4. 面板应显示最近 10 条记录，每条包含：时间、成功/失败标签、耗时
5. 再次点击时钟图标，面板应收起

**预期效果**: 面板正常展开/收起，数据正确

---

---

## 手动验证补充发现的 i18n 问题

以下问题在用户切换英文环境后手动发现，需后续修复。

### F5: SchedulerView — CHANNEL_LABELS 硬编码中文

**文件**: `apps/setup-center/src/views/SchedulerView.tsx:120-135`

```typescript
const CHANNEL_LABELS: Record<string, string> = {
  telegram: "Telegram",
  dingtalk: "钉钉",       // → 英文应为 "DingTalk"
  feishu: "飞书",         // → 英文应为 "Feishu" 或 "Lark"
  wework: "企业微信",      // → 英文应为 "WeCom"
  wework_ws: "企业微信",   // → 同上
  wework_bot: "企业微信",  // → 同上
  qqbot: "QQ",
  onebot: "QQ(OneBot)",
  onebot_reverse: "QQ(OneBot)",
  wechat: "微信",         // → 英文应为 "WeChat"
  ...
};
```

**修复方向**: 将 `CHANNEL_LABELS` 改为函数，内部用 `t("scheduler.channelXxx")` 从 i18n 取值，或在 en.json/zh.json 中增加对应 key。

### F6: SchedulerView — 任务名称/内容不做翻译

系统内置任务的 `name` 和 `description` 是中文（如"能力验证提醒"、"活人感心跳"），这些在后端 Python 代码中硬编码。在英文环境下仍显示中文。

**影响范围**: `scheduler/scheduler.py` 或 `core/agent.py` 中创建系统任务时的 `name`/`description` 字段。

**修复方向**: 系统任务名称做 i18n 映射（前端侧 fallback 或后端返回 i18n key）。

### F7: MemoryView — 页面未国际化

**文件**: `apps/setup-center/src/views/MemoryView.tsx`

页面中大量中文 UI 文案未走 i18n：
- 统计栏: "总记忆数"、"平均分数"、"事实"、"技能"、"规则"、"偏好"、"经验教训"、"人格特征"、"经验"
- 表头: "类型"、"内容"、"分数"、"创建时间"、"操作"
- 搜索框: "搜索记忆内容..."
- 按钮: "刷新"、"LLM 智能审查"、"列表"、"图谱"
- 类型标签: "事实"、"fact" 等

**注意**: 记忆内容本身不需要翻译（用户数据）。

**i18n 现状**: 仅有 `sidebar.memory` 键，**无 `memory` 命名空间**。需新建整套 `memory.*` i18n keys。

### F8: OrgEditorView — 页面未国际化

**文件**: `apps/setup-center/src/views/OrgEditorView.tsx`

- "组织编排" 标题
- "选择或创建一个组织开始编排"
- "内容运营团队"、"7 节点 · dormant"

**i18n 现状**: 仅有 `sidebar.orgEditor` 键，**无 `org` 命名空间**。

### F9: PixelOfficeView — 页面未国际化

**文件**: `apps/setup-center/src/views/PixelOfficeView.tsx` + `components/pixel-office/`

- "公共区域"、"部门·创化组"、"部门·运营组"、"公会室"
- "事件日志"、"成员"、"场景主题"
- "等待组织事件..."、"收起面板"

**i18n 现状**: **完全无** `pixelOffice` 相关 i18n keys，所有文案硬编码。

### F10: AgentManagerView — 分类名未国际化

**文件**: `apps/setup-center/src/views/AgentManagerView.tsx`

Agent Manager 页面主体已基本英文化（"Agent Manager"、"Refresh"、"Import"、"Create Agent"），但分类标签仍为中文：
- "通用基础"、"内容创作"、"企业办公"、"教育辅助"、"生活效率"、"开发运维"

这些分类名可能来自后端 Agent profile 数据的 `category` 字段。

### F11: StatusView — "QQ 机器人" 硬编码

**文件**: `apps/setup-center/src/views/StatusView.tsx:96`

```typescript
{ k: "QQBOT_ENABLED", name: "QQ 机器人", ... }
```

英文环境应为 "QQ Bot"。

---

### F12: (高) 多步推理中间文本以正文输出导致刷屏

**严重程度**: 高 (UX 问题)

**现象**: 用户发送 "创建定时任务" 请求后，Agent 执行了 **102 步迭代** (77 个 LLM 请求，消息数从 7 增长到 76)。每一步的中间文本（如"我来帮你创建这个定时任务"、"让我换一个方法"）都以**正文形式输出**，导致：
1. 回复区域被大量中间思考文字淹没（刷屏）
2. "处理中"加载状态指示器被推出视口，用户误以为系统卡死
3. 理论上这些中间文本应被折叠到"102 步推理过程"区域内

**根因分析** (代码级定位):

```
reasoning_engine.py:2122-2124 (stream_react 方法)
```

```python
elif _evt_type == "text_delta":
    yield stream_event          # ← 无条件转发为 text_delta
    _streamed_text = True
```

在 `stream_react` 的流式路径中，每个迭代从 `_reason_stream` 获取的 `text_delta` 事件被**无条件直接转发**给前端。前端对 `text_delta` 的处理是追加到 `currentContent`（正文区域），而 `chain_text` 才会进入折叠的思考链。

```
ChatView.tsx:2009-2011
```
```typescript
case "text_delta":
    currentContent += event.content;  // ← 追加到正文
    break;
```

虽然后端在非流式 fallback 路径 (line 2213-2216) 对工具调用迭代会正确发 `chain_text`，但流式路径先于 fallback 执行，text 已经作为 `text_delta` 发出，无法追回。

Line 2355-2357 的 VERIFY 分支有正确处理（先 `text_replace("")` 清空，再发 `chain_text`），但主工具调用分支缺少类似逻辑。

**LLM 日志证据**:
- 77 个请求文件，caller 为 `messages_create_stream`
- 消息数从 7 → 9 → 11 → ... → 76（每次迭代 +2-3 条）
- 17 条 `[系统提示]` 注入（重复工具调用警告），说明 Agent 反复尝试
- 最终 `messages_create_stream` 请求时间跨度：15:17 ~ 15:28 (约 11 分钟)

**修复方向**:
1. 在 `stream_react` 的流式路径中，对**非最终迭代**（decision.type == TOOL_CALLS）的 `text_delta` 进行拦截：先缓冲，待 decision 确定后改为 `chain_text` 发送
2. 或：在获得 decision 后，如果 type 是 TOOL_CALLS 且 `_streamed_text == True`，追加 `text_replace("")` + `chain_text`（与 VERIFY 分支行为一致）
3. 前端侧：在长时间无新 SSE 事件时保持"处理中"指示器可见

---

## 发现问题完整汇总（含手动验证）

| ID | 严重度 | 类型 | 描述 |
|----|--------|------|------|
| **F12** | **高** | **UX** | **多步推理中间文本以 text_delta 输出导致刷屏 (reasoning_engine.py:2122)** |
| F1 | 低 | 功能 | `query_task_executions` 工具在对话中未被 LLM 选用 |
| F2 | 低 | i18n | `scheduler.missedHint` key 已定义但代码未引用（死文案） |
| F3 | 信息 | 设计 | 所有错误响应使用 HTTP 200 + `{error}` |
| F4 | 低 | 校验 | 后端不校验任务名称非空 |
| F5 | 中 | i18n | SchedulerView CHANNEL_LABELS 硬编码中文（钉钉/飞书/企业微信） |
| F6 | 低 | i18n | 系统任务名称/描述未国际化 |
| F7 | 中 | i18n | MemoryView 整页未国际化（需新建 memory.* namespace） |
| F8 | 中 | i18n | OrgEditorView 整页未国际化（需新建 org.* namespace） |
| F9 | 中 | i18n | PixelOfficeView 整页未国际化（需新建 pixelOffice.* namespace） |
| F10 | 低 | i18n | AgentManagerView 分类名未国际化 |
| F11 | 低 | i18n | StatusView "QQ 机器人" 硬编码 |

---

## 结论

**后端 API 层**: 全部 28 次调用通过，所有新增功能（执行记录追加存储、执行历史分页查询、toggle 响应、missed 状态、fail_count 字段）均正常工作。

**对话集成**: 3 次对话调用通过，AI 能正确列出任务和概述执行状况，但 `query_task_executions` 工具在特定场景下未被 LLM 选用（F1）。

**代码审查**: Bug1（safe_write 导入）和 Bug2（useCallback 优化）均已确认修复。i18n 16 个 scheduler key 全部存在于中英文 locale 文件。

**手动验证补充**: 发现 7 个 i18n 问题（F5-F11），涉及 SchedulerView 通道名、MemoryView、OrgEditorView、PixelOfficeView、AgentManagerView 分类、StatusView 的中文硬编码。

**需要手动验证**: 7 个前端 UI 场景（详见上文），涉及视觉元素（颜色、图标、toast）和交互行为（展开/收起、校验弹窗），无法通过 API 自动化测试。
