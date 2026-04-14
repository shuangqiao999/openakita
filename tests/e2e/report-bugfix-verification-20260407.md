# 修复验证测试报告

> **测试日期**: 2026-04-07 16:54 ~ 17:21  
> **测试环境**: 后端 v1.27.9 (Python 3.9.6), 前端 Vite dev server (localhost:5173)  
> **Conv ID**: `4a0f1363-1203-4311-91c9-4c45e84533f0`  
> **测试类型**: AI 探索性测试 (22 轮多轮对话) + LLM 日志审计  
> **测试日志**: `tests/e2e/_test_log_20260407_1654.jsonl`  
> **LLM 调试日志**: `data/llm_debug/llm_request_20260407_*.json` (293 个文件)

---

## 一、测试执行概要

| 指标 | 值 |
|------|-----|
| 总轮次 | 22 |
| 总耗时 | 1610.3s (~27 分钟) |
| 错误轮次 | 0 |
| ask_user 中断 | 0 |
| 空回复 | 0 |
| 慢轮次 (>60s) | 3 (Turn 8: 1001.8s, Turn 13: 60.6s, Turn 22: 103.4s) |
| LLM 请求总数 | 293 |
| 工具调用总数 | ~40 次 |
| 子 Agent 委派 | 73 条委派日志 |
| 安全策略决策 | 896 条 |

---

## 二、System Prompt 审计

### 2.1 系统提示词结构检查

| 检查项 | 状态 | 说明 |
|--------|------|------|
| `## 当前会话` 元数据 | ✅ | session_id、通道、消息数均正确 |
| `## 系统概况` | ✅ | 包含 `powered by {model}` 动态模型名 |
| `## 对话上下文约定` | ✅ | 时间戳注入规则完整 |
| `## 记忆系统` (三级优先级) | ✅ | 信息优先级、记忆层级说明完整 |
| 无"仅供参考"字样 | ✅ | 已清除 |
| 语言适配 (language) | ✅ | 包含语言规则（最高优先级） |

### 2.2 Messages 结构检查

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 历史消息带 `[HH:MM]` 时间戳 | ✅ | 从第 2 轮起正确注入 |
| 最后用户消息带 `[最新消息]` 前缀 | ✅ | 后期大文件确认存在 |
| 无双重时间戳 `[HH:MM] [HH:MM]` | ✅ | 未发现 |
| 消息顺序正确 | ✅ | 与实际对话一致 |

### 2.3 工具定义检查

| 工具 | 状态 | 说明 |
|------|------|------|
| `get_session_context` | ✅ | |
| `delegate_to_agent` (含 context 参数) | ✅ | |
| `delegate_parallel` (含 context 参数) | ✅ | |
| `org_delegate_task` | ❌ **缺失** | **见问题 P1** |
| `search_memory` / `add_memory` | ✅ | |
| `update_user_profile` | ✅ | |
| `create_todo` / `tool_search` | ✅ | |
| `run_shell` / `run_powershell` | ✅ | |
| `setup_organization` | ✅ | |
| 工具总数 | 123 | |

---

## 三、对话表现分析

### Phase 1: 基础对话 + 记忆系统 (Turn 1-7)

| Turn | 耗时 | 工具 | 结果 |
|------|------|------|------|
| 1 | 15.0s | (none) | ✅ 正确问候，列出已了解信息 |
| 2 | 20.1s | `add_memory` | ✅ 正确存储项目信息 |
| 3 | 12.5s | (none) | ✅ 纯记忆召回，正确列出所有信息 |
| 4 | 12.2s | (none) | ✅ 正确更新：8人+MongoDB |
| 5 | 16.9s | `sleep` | ⚠️ 话题跳转回答正确，但不必要地调用了 `sleep` |
| 6 | 10.7s | (none) | ✅ 远距离回溯正确：8人+MongoDB |
| 7 | 11.5s | (none) | ✅ 正确识别混淆，未被骗 |

**发现的问题:**

- **[P5] Turn 1 未触发 `add_memory`**: 用户首次提供姓名、城市、项目等关键信息，系统未主动存入长期记忆，仅依赖对话历史。Turn 2 提供更多信息时才触发 `add_memory`
- **[P6] Turn 4 信息纠正未触发记忆更新**: 用户明确纠正"8人+MongoDB"，系统口头确认但未调用 `add_memory` 或 `update_user_profile`。导致 Turn 22 的记忆搜索中发现 PostgreSQL 和 MongoDB 两条冲突记录
- **[P7] Turn 5 不必要的 `sleep` 工具调用**: 一个简单的知识问答（await vs yield）不应需要 sleep 工具

### Phase 2: Plan/Todo + 技能 + 工具 (Turn 8-11)

| Turn | 耗时 | 工具 | 结果 |
|------|------|------|------|
| 8 | **1001.8s** | create_todo, delegate_parallel, update_todo_step×5, delegate_to_agent | ❌ **严重超时** |
| 9 | 36.7s | list_skills | ✅ 列出 182 个技能 |
| 10 | 53.5s | web_search, web_fetch×2 | ✅ 搜索到 FastAPI 2026 新特性 |
| 11 | 45.2s | create_todo, update_todo_step×2 | ✅ 复杂任务触发了 plan 模式 |

**发现的问题:**

- **[P1] Turn 8: 创建 Todo 列表触发了不必要的全面委派 (严重)**: 
  - **现象**: 用户仅要求"创建一个 todo 列表"，系统不仅创建了 todo，还自动发起了 `delegate_parallel`（5个并行子 Agent）和 `delegate_to_agent`，导致耗时 1001.8s (约17分钟)
  - **根因分析**: 
    1. 系统注入了 `[系统] ⚠️ 你的上一条回复没有调用任何工具（tool_calls=0）` 推动消息，在 `create_todo` 已调用的情况下仍然要求LLM执行更多工具
    2. 后续又注入 `[系统提示] 当前 Plan 仍有未完成的步骤。请立即继续执行下一个 pending 步骤` 推送 LLM 继续执行 plan steps
    3. LLM 被系统推动后开始委派实际开发任务给子 Agent（architect、code-assistant、devops-engineer）
    4. 直到检测到 `连续 5 轮仅调用管理类工具` 后才回滚
  - **日志证据**: 73 条委派日志、53 次 `messages_create_async`（子Agent调用）、21 次 `compiler_think`
  - **涉及文件**: `agent.py` (系统推送逻辑)、`tool_handler.py` (todo 步骤推进)
  - **影响**: 用户体验极差，简单操作被放大为复杂的多 Agent 协作

- **[P8] Turn 11: 复杂任务 Plan 建议行为**: 
  - 复杂任务（微服务重构）触发了 `create_todo` 和步骤更新，说明 plan 建议机制生效
  - 但回复中出现 3 次重复的开头语（"好的，张明！"），说明存在多次迭代/重试
  - 这可能是 prompt 中 `text_replace` 事件导致的文本替换行为

### Phase 3: 组织编排 (Turn 12-14)

| Turn | 耗时 | 工具 | 结果 |
|------|------|------|------|
| 12 | 26.0s | setup_organization | ✅ 列出 4 个现有组织 |
| 13 | 60.6s | setup_organization×2, call_mcp_tool, write_file | ⚠️ 未使用 org 编排，走了 MCP 备选路径 |
| 14 | 32.1s | get_session_context, list_recent_tasks | ⚠️ 任务状态 "ongoing" 但未完成 |

**发现的问题:**

- **[P2] `org_delegate_task` 工具未注册到工具列表 (严重)**:
  - **现象**: 组织编排的核心工具 `org_delegate_task` 不在 123 个工具列表中（❌ 缺失），导致 LLM 无法通过正常路径向组织节点委派任务
  - **替代行为**: LLM 尝试使用 `setup_organization`（2次冗余调用）和 `call_mcp_tool`（企业微信 MCP）作为变通方案，但 MCP 连接不可用，最终退化为 `write_file` 写一个任务文档
  - **根因**: 工具注册逻辑可能缺少对 `org_delegate_task` 的条件注册（可能需要活跃组织才注册）
  - **涉及文件**: `tools/handlers/org_tools.py` 或 `tool_registry.py`
  - **影响**: 整个组织编排流程无法正常触发，render 之前修复的 chain_id、gather、watchdog 等逻辑全部无法验证

- **[P9] 组织事件存储为空**: 
  - `data/orgs/*/events/` 目录下无任何事件文件（0 events）
  - 说明 `OrgEventStore.emit()` 在测试期间从未被调用
  - 与 `org_delegate_task` 缺失直接相关

### Phase 4: 安全策略 + Shell 执行 (Turn 15-17)

| Turn | 耗时 | 工具 | 结果 |
|------|------|------|------|
| 15 | 19.2s | run_shell | ✅ `python --version` → Python 3.9.6 |
| 16 | 26.2s | run_powershell | ✅ `dir C:\Windows\System32` 执行成功 |
| 17 | 24.0s | read_file | ✅ pyproject.toml 正确读取 |

**发现的问题:**

- **[P10] Turn 16: 系统目录访问无安全提醒**: 
  - `dir C:\Windows\System32` 虽然是只读操作，但安全策略审计日志中记录为 `action=allow, risk=?`
  - 风险等级字段为空（`?`），说明 `_HIGH_RISK_SHELL_PATTERNS` 匹配逻辑可能未正确填充 risk_level
  - 策略决策审计缺少 `risk_level` 字段值

### Phase 5: 综合测试 + 记忆验证 (Turn 18-22)

| Turn | 耗时 | 工具 | 结果 |
|------|------|------|------|
| 18 | 37.3s | list_mcp_servers | ✅ 列出 4 个 MCP 服务器 |
| 19 | 7.6s | (none) | ✅ 简短回复 |
| 20 | 7.1s | (none) | ⚠️ 与 Turn 19 完全相同的回复 |
| 21 | 30.7s | (none) | ✅ 综合总结准确，正确识别混淆测试 |
| 22 | 103.4s | search_memory×3, list_recent_tasks | ⚠️ 记忆搜索较慢，发现数据不一致 |

**发现的问题:**

- **[P3] Turn 19-20: 会话消息去重未生效 (中等)**:
  - **现象**: 连续两次发送"好的"（Turn 19 和 Turn 20），两次都生成了完全相同的 164 字符回复
  - **验证**: 对话历史 JSONL 文件确认两条 `"好的"` 消息（line 48 和 line 50）均被持久化:
    - `line 48`: role=user, content="好的", timestamp=17:18:48.720
    - `line 50`: role=user, content="好的", timestamp=17:18:56.308
    - 两条消息仅间隔 7.6s（Turn 19 处理时间），内容完全一致
  - **深度分析**:
    1. `session.py:add_message` 的滑动窗口去重逻辑在代码层面正确（fingerprint 仅基于 `role:content[:200]`）
    2. 对话历史 JSONL 文件（`data/memory/conversation_history/`）的写入路径可能独立于 `session.add_message`，由 memory consolidator 或 session manager 的 `_save_sessions` 持久化，二者可能不共享去重状态
    3. 另一种可能：Turn 20 的 HTTP 请求到达时，Turn 19 的 assistant 消息尚未通过 `add_message` 保存（`chat.py` 中 assistant 消息保存在 streaming 完成后的 finally 块中），但 Turn 19 的 **user** 消息已在窗口中，理论上应被拦截
    4. 可能存在 asyncio 并发的时序问题：`_msg_lock` 是 `threading.RLock`，对同一事件循环内的协程无阻塞效果
  - **涉及文件**: `sessions/session.py:add_message`, `sessions/manager.py:_save_sessions`, `api/routes/chat.py`

- **[P4] Turn 22: 记忆存储存在冲突记录 (中等)**:
  - **现象**: 记忆搜索结果中同时存在:
    - `[fact] CloudSync: 后端 FastAPI, 数据库 PostgreSQL` (过时)
    - `[fact] CloudSync: MongoDB` (已更新)
  - **根因**: Turn 4 的信息纠正（8人+MongoDB）未触发 `add_memory` 或 `update_user_profile`，旧记忆未被更新或标记为过时
  - **额外发现**: 记忆中同时存在"张明"和"小明"两条记录（来自不同会话），系统未自动合并

---

## 四、问题汇总与严重程度

### P0 — 阻塞性问题

| # | 问题 | 严重程度 | 影响范围 | 关联清单项 |
|---|------|----------|----------|------------|
| P2 | 主 Agent 缺少"向组织发送命令"的工具（`org_delegate_task` 仅为组织内部工具） | **P0 Critical** | 用户无法通过对话触发组织编排 | 清单 #1-#11 无法通过对话验证（API 直接调用已验证正常） |

### P1 — 严重问题

| # | 问题 | 严重程度 | 影响范围 | 关联清单项 |
|---|------|----------|----------|------------|
| P1 | Todo 创建触发不必要的全面 Agent 委派（17分钟超时） | **P1 High** | 用户体验 + 资源浪费 | 系统推送机制 |

### P2 — 中等问题

| # | 问题 | 严重程度 | 影响范围 | 关联清单项 |
|---|------|----------|----------|------------|
| P3 | 会话消息去重未拦截连续相同消息 | **P2 Medium** | 消息去重 | 清单 #24-#27 |
| P4 | 记忆存储存在冲突记录（PostgreSQL vs MongoDB） | **P2 Medium** | 记忆一致性 | 清单 #12-#18 |

### P2 — 中等问题（补充）

| # | 问题 | 严重程度 | 影响范围 | 关联清单项 |
|---|------|----------|----------|------------|
| P11 | LLM 口头说"已记住"但未调用 `add_memory` | **P2 Medium** | 记忆持久化可靠性 | 清单 #12 |
| P12 | 重复记忆写入请求未被 FTS5 去重拦截 | **P2 Medium** | 记忆去重 | 清单 #14 |
| P13 | 高危命令检测到风险但仍然执行（未真正阻止） | **P2 Medium** | 安全策略 | 清单 #29 |

### P3 — 低优先级问题

| # | 问题 | 严重程度 | 影响范围 |
|---|------|----------|----------|
| P5 | 首轮信息未触发 `add_memory` | **P3 Low** | 首轮记忆持久化 |
| P6 | 信息纠正未触发记忆自动更新（两轮测试均确认） | **P3 Low** | 记忆更新流程 |
| P7 | 简单问题不必要的 `sleep` 工具调用 | **P3 Low** | LLM 工具选择准确性 |
| P8 | 多轮迭代导致回复开头重复 | **P3 Low** | 输出质量 |
| P9 | 组织事件存储为空（无 events） | **P3 Low** | 依赖 P2 修复 |
| P10 | 安全策略审计 risk_level 字段为空 | **P3 Low** | 审计完整性 |
| P14 | 记忆请求与之前对话的未完成请求混合执行 | **P3 Low** | 工具调用精确性 |

---

## 五、清单项验证状态

### 已验证通过 ✅

| 清单# | 项目 | 状态 |
|--------|------|------|
| #19 | 简单任务不建议 Plan | ✅ Turn 5 无 plan 建议 |
| #20 | 复杂任务建议 Plan | ✅ Turn 11 触发了 plan 创建 |
| #28 | `require_confirmation` 标志 | ✅ 所有工具 allow（未配置 confirmation 规则） |
| #33 | 终端命令执行 | ✅ Turn 15 python --version 正常 |
| #39 | Shell 命令超时 | ✅ 未触发超时 |
| #40 | PowerShell 执行 | ✅ Turn 16 dir 正常 |
| #66 | 静态 prompt 缓存 | ✅ 多轮对话 prompt 结构稳定 |
| #67 | 语言适配 | ✅ system prompt 包含语言规则 |
| #70 | 正常对话流程 | ✅ 22 轮无中断、无异常退出 |

### 无法验证 ❌ (因 P2 阻塞)

| 清单# | 项目 | 原因 |
|--------|------|------|
| #1 | 单次委派 | `org_delegate_task` 未注册 |
| #2 | 并行委派 | 同上 |
| #3 | 子链超时处理 | 同上 |
| #4 | gather 超时 | 同上 |
| #5 | 链追踪内存清理 | 同上 |
| #6-#9 | 看门狗/熔断器 | 同上 |
| #10-#11 | 消息通信 | 同上 |

### 需进一步验证 ⚠️

| 清单# | 项目 | 状态 |
|--------|------|------|
| #12-#15 | 内存写入去重 | 部分验证（发现冲突记录） |
| #16-#18 | _memories 一致性 | 需单元测试验证 |
| #24-#27 | 会话消息去重 | ❌ 连续"好的"未被去重 |
| #29 | 高危 shell 命令检测 | 未测试写入操作 |
| #30-#36 | P0 TimeoutError 路径 | 需 Python 3.9 专项测试 |
| #68 | 技能渐进披露 | 未专门验证 |

---

## 六、LLM 日志审计详细发现

### 6.1 Turn 8 委派风暴时序分析

```
16:56:00  Turn 7 处理完成（混淆测试）
16:56:11  Turn 8 开始：create_todo 请求
16:56:23  LLM 返回 create_todo 工具调用 + 文本描述
16:56:28  ⚠️ 系统注入："你的上一条回复没有调用任何工具(tool_calls=0)"
          → LLM 被迫继续调用工具
16:56:48  delegate_parallel 调用（5个子Agent）
16:57:xx  5个 ephemeral Agent 开始并行执行
17:01:53  系统注入："Plan 仍有未完成步骤，请继续"
17:02:25  系统注入："Plan 仍有未完成步骤，请继续" (再次)
17:02:37  ⚠️ 系统回滚："连续5轮仅调用管理类工具"
17:12:47  子Agent 全部完成或超时
17:12:58  Turn 8 最终返回结果
```

**总计**: 53 次子 Agent LLM 调用, 21 次编译思考, 73 条委派日志

### 6.2 System Prompt 增长趋势

| 时间 | 文件大小 | 系统提示词长度 | 消息数 | 工具数 |
|------|----------|---------------|--------|--------|
| 16:54 (Turn 1) | 157.9KB | 28,909 ch | 3 | 123 |
| 17:20 (Turn 21) | 261.1KB | 63,104 ch | 47 | 123 |

System prompt 在 22 轮对话后增长了 **2.18 倍** (28.9K → 63.1K 字符)，主要是记忆注入、技能目录和 AGENTS.md 内容的累积。

### 6.3 策略审计

- 896 条策略决策记录，全部为 `action=allow`
- **risk_level 字段全部为空**（`?`），说明 `PolicyEngine._check_legacy_tool_policy` 未正确填充风险等级
- 未触发任何 `require_confirmation` 或 `deny` 决策

---

## 七、修复建议优先级

### 立即修复 (P0)

1. **为主 Agent 增加"向组织发送命令"的工具**
   - `org_delegate_task` 是组织**内部**工具（节点间委派），设计上不应暴露给主 Agent
   - 需要新增一个用户面向的工具（如 `send_org_command`），让主 Agent 可以调用 `POST /api/orgs/{org_id}/command`
   - 或者在 `setup_organization` 中增加 `send_command` action
   - 该工具应接受 org_id、目标节点（可选）、命令内容等参数
   - API 层 `POST /api/orgs/{id}/command` 已验证正常工作，只需桥接到工具层
   - 修复后可通过对话验证清单 #1-#11 的所有组织编排测试项

### 高优先级 (P1)

2. **修复 Todo 创建场景的系统推送逻辑**
   - 当 `create_todo` 已被调用时，`tool_calls=0` 推送不应触发
   - 建议排除管理类工具调用（create_todo, update_todo_step）后再判断是否需要推送
   - "Plan 仍有未完成步骤" 推送应区分"用户请求创建 plan"和"用户请求执行 plan"

### 中优先级 (P2)

3. **修复会话消息去重**
   - 检查 `session.py:add_message` 的 MD5 指纹计算是否包含了时间戳等元数据
   - 确保指纹仅基于纯文本内容（去除 `[HH:MM]` 前缀后）

4. **修复记忆纠正自动更新**
   - 当用户明确纠正信息时，系统应自动调用 `update_user_profile` 或更新已有记忆
   - 至少应将旧记忆标记为"已过时"

### 低优先级 (P3)

5. 完善策略审计日志的 risk_level 字段
6. 优化 LLM 多轮迭代导致的重复开头语
7. 减少不必要的工具调用（如 sleep）

---

## 八、附录

### A. 测试轮次明细

| Turn | 耗时 | 工具 | 状态 | 主题 |
|------|------|------|------|------|
| 1 | 15.0s | - | OK | 基础问候+信息注入 |
| 2 | 20.1s | add_memory | OK | 追加项目信息 |
| 3 | 12.5s | - | OK | 信息召回（无工具） |
| 4 | 12.2s | - | OK | 信息纠正 |
| 5 | 16.9s | sleep | ⚠️ | 话题跳转 |
| 6 | 10.7s | - | OK | 远距离回溯 |
| 7 | 11.5s | - | OK | 故意混淆测试 |
| 8 | 1001.8s | create_todo, delegate_parallel, ... | ❌ SLOW | Todo 创建+不必要委派 |
| 9 | 36.7s | list_skills | OK | 技能列表 |
| 10 | 53.5s | web_search, web_fetch×2 | OK | 搜索功能 |
| 11 | 45.2s | create_todo, update_todo_step×2 | OK | 复杂任务+Plan |
| 12 | 26.0s | setup_organization | OK | 组织列表 |
| 13 | 60.6s | setup_organization×2, call_mcp_tool, write_file | ⚠️ SLOW | 组织任务委派（失败） |
| 14 | 32.1s | get_session_context, list_recent_tasks | OK | 任务进度查询 |
| 15 | 19.2s | run_shell | OK | Python 版本 |
| 16 | 26.2s | run_powershell | OK | 系统目录列表 |
| 17 | 24.0s | read_file | OK | 读取 pyproject.toml |
| 18 | 37.3s | list_mcp_servers | OK | MCP 服务器列表 |
| 19 | 7.6s | - | OK | 短消息 "好的" |
| 20 | 7.1s | - | ⚠️ | 重复短消息（去重未生效） |
| 21 | 30.7s | - | OK | 综合总结 |
| 22 | 103.4s | search_memory×3, list_recent_tasks | ⚠️ SLOW | 记忆搜索验证 |

### B. 关键日志文件位置

| 内容 | 路径 |
|------|------|
| 测试结构化日志 | `tests/e2e/_test_log_20260407_1654.jsonl` |
| LLM 调试日志 | `data/llm_debug/llm_request_20260407_*.json` |
| 委派日志 | `data/delegation_logs/20260407.jsonl` |
| 策略审计 | `data/audit/policy_decisions.jsonl` |
| 对话历史 | `data/memory/conversation_history/desktop__4a0f1363-*.jsonl` |
| 工具溢出日志 | `data/tool_overflow/delegate_parallel_20260407_*.txt` |
| 子 Agent 追踪 | `data/tool_overflow/trace_summary_20260407_*.txt` |
| 回顾日志 | `data/retrospects/2026-04-07_retrospects.jsonl` |

### C. 补充测试结果（第二轮 6 轮对话）

> **Conv ID**: `f8be2508-a6f5-4926-8043-b75980426161`  
> **日志**: `tests/e2e/_test_log_supp_20260407_1733.jsonl`

#### C.1 高危 Shell 命令检测（清单 #29）

| 测试 | 命令 | 结果 |
|------|------|------|
| S1 | `echo 'test' > C:\Windows\temp\test.txt` | ⚠️ **检测到风险但仍然执行** |

- LLM 回复中包含 "系统相关路径" + "为了安全起见，我需要您的确认"
- 但工具仍调用了 `run_powershell`
- **发现**: 安全策略识别了写入系统目录的风险，但没有真正阻止执行。`require_confirmation` 机制似乎依赖 `ask_user` 流程，而在 SSE 单向流中无法有效中断

#### C.2 技能渐进披露（清单 #68）

| 测试 | 消息 | 工具 | 结果 |
|------|------|------|------|
| S2 | "介绍一下FastAPI"（无技能关键词） | (none) | ✅ 无技能目录展开 |
| S3 | "用图片生成技能画logo"（含技能关键词） | (none) | ✅ 识别到技能需求，询问细节 |

渐进披露行为正常：不提及技能时不主动展示，提及时正确响应。

#### C.3 记忆去重写入（清单 #12-#15）

| 测试 | 消息 | add_memory | 结果 |
|------|------|-----------|------|
| S4 | "请记住：FinTech Pro, Django+PostgreSQL, 阿里云" | ❌ 未调用 | **问题**：LLM口头说"已记住"但未真正持久化 |
| S5 | 相同内容再次请求记忆 | ✅ 调用 | **问题**：去重未生效，第二次才真正存储 |

- **[P11] 首次明确的记忆存储请求未触发 `add_memory`**: 用户明确说"请记住"，LLM 回复"已保存到记忆中"，但实际上未调用 `add_memory` 工具
- **[P12] 第二次相同内容记忆请求未被去重**: `add_memory` 被调用了，且 reply 中无"已存在"/"重复"提示，说明 FTS5 去重可能未拦截
- S5 额外问题：系统在记忆请求的同时，意外触发了 `generate_image`, `run_skill_script`, `write_file`, `run_shell` 等工具——把之前提到的 logo 生成请求也混在一起执行了

#### C.4 信息纠正记忆自动更新

| 测试 | 消息 | 记忆更新工具 | 结果 |
|------|------|-------------|------|
| S6 | "改用 Spring Boot+MySQL" | ❌ [] | **确认 P6**：信息纠正不触发记忆持久化更新 |

#### C.5 组织 API 直接调用测试

| API | 结果 |
|-----|------|
| `GET /api/orgs` | ✅ 200, 4 个组织 |
| `POST /api/orgs/{id}/command` | ✅ 200, `{"command_id": "feb626786828", "status": "running"}` |

**关键发现**: 组织编排 API（`/api/orgs/{id}/command`）可以**直接通过 HTTP 正常调用**并成功派发任务。

**命令执行验证**:
```
POST /api/orgs/org_42895da52ade/command
  → {"command_id": "feb626786828", "status": "running"}

GET /api/orgs/org_42895da52ade/commands/feb626786828
  → {"status": "done", "result": {"node_id": "tech-lead", "result": "登录页面原型已完成..."}}
```

- tech-lead 节点成功接收并执行任务，创建了 `login-page-prototype.html`
- 所有 8 个节点（tech-lead, fe-lead, fe-dev-a/b, be-lead, be-dev-a/b, qa）在任务完成后全部回到 **idle** 状态
- inbox 消息为空（已处理）

这证明:
1. ✅ **组织运行时（OrgRuntime）完全正常**——节点调度、任务执行、状态管理均工作
2. ✅ **之前修复的基础设施已生效**——节点正确回到 idle（非 ERROR/FROZEN），无残留状态
3. ❌ **问题仅在于主 Agent 没有工具来调用 `/api/orgs/{id}/command`**——`org_delegate_task` 是组织内部工具（节点间委派），主 Agent 的 123 个工具中缺少一个"向组织发送命令"的入口工具

### D. 对比上次测试改善项

| 指标 | 上次 (04-05/06) | 本次 (04-07) | 变化 |
|------|-----------------|-------------|------|
| ask_user 中断 | 多次 | 0 次 | ✅ 改善 |
| Plan 建议机制 | 过度触发 | 仅复杂任务触发 | ✅ 改善 |
| 混淆测试识别 | 部分被骗 | 完全识别 | ✅ 改善 |
| 双重时间戳 | 存在 | 0 处 | ✅ 修复 |
| 系统提示词"仅供参考" | 存在 | 已清除 | ✅ 修复 |
| 消息去重 | 未实现 | 部分生效 | ⚠️ 仍有问题 |
| 组织编排 | 超时卡死 | 工具未注册 | ❌ 新问题 |
