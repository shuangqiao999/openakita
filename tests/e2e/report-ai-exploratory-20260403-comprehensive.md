# OpenAkita AI 探索性全面自测报告（2026-04-03）

- **依据规范**: `ai-exploratory-testing.mdc`（真实多轮 SSE 对话 + 日志审计）
- **后端地址**: `http://127.0.0.1:18900`
- **执行方式**: 使用 `tests/e2e/_ai_exploratory_comprehensive_20260403.py` 驱动 `POST /api/chat`（`httpx` 流式），共 **20 轮主会话** + **Plan 模式 1 轮** + **Ask 模式 1 轮**（合计 **22 次** API 调用）
- **运行日志**: `tests/e2e/_ai_exploratory_comprehensive_20260403.run.log`
- **健康检查**: `GET /api/health` → `status: ok`，`agent_initialized: true`，版本 `1.27.7+unknown`

---

## 1. 测试矩阵（覆盖点）

| 维度 | 设计内容 | 观察摘要 |
|------|----------|----------|
| 事实记忆 / 纠正 | 项目 Delta、负责人、预算、子系统 | 多轮复述与纠正（李娜→王强，120→150 万）基本正确 |
| 计算追问 | 季度均分、5% 应急金 | 数值正确（30 / 31.5） |
| 话题跳转 | TCP 慢启动 | 能回答并回到 Delta |
| 远距离回溯 | 第 5 轮预算口径 | 能区分「更新前 120 万」与纠正后预算 |
| 故意混淆 | 「负责人在上海」「Rust」 | 否定错误前提；Rust 题外显检索记忆，区分 StarBridge |
| Todo / 长任务 | `create_todo` 安全审计 6 步 | 成功创建；`get_todo_status` + `update_todo_step` 将第 1 步标为进行中 |
| 长文本 | ~4KB 重复段 +「末尾三字」 | **模型答「重复块」，与文本真实结尾不一致**（见 §4） |
| 图片 | `data:image/png;base64` 1×1 PNG | **出现 `type:error` 与 `[SSE_ERROR: ]`，多模态失败** |
| 技能 / 插件（对话侧） | 询问 skills/plugins | 模型列举技能索引与 MCP 等（依赖注入 prompt，非 HTTP 实测） |
| Plan 模式 | `mode=plan`，K8s 迁移三阶段 | 产出三阶段 + 验收标准；调用了 `search_memory`、`glob` |
| Ask 模式 | `mode=ask`，幂等性 | 纯文本要点，**未调工具**，符合只读预期 |
| 综合总结 | 会话要点汇总 | 结构化总结正确 |

---

## 2. 过程与关键日志位置

### 2.1 终端 / 脚本输出

- **完整运行输出**: `tests/e2e/_ai_exploratory_comprehensive_20260403.run.log`
- **驱动脚本**: `tests/e2e/_ai_exploratory_comprehensive_20260403.py`  
  - 首轮若未设置 UTF-8，Windows 控制台在打印含 emoji 的模型回复时会报 `UnicodeEncodeError`（已在脚本中增加 `PYTHONIOENCODING` + `stdout.reconfigure(utf-8)` 规避）。

### 2.2 会话 ID（便于对照会话存储）

| 用途 | `conversation_id` |
|------|---------------------|
| 主流程（20 轮） | `ai_explore_20260403_main_v2` |
| Plan 模式 | `ai_explore_20260403_planmode_v2` |
| Ask 模式 | `ai_explore_20260403_askmode_v2` |

### 2.3 LLM Debug（`data/llm_debug/`）

| 文件 | 说明 |
|------|------|
| `llm_request_20260403_121437_2b2a6c09.json` | **主会话**较大请求之一（`system` 约 50002 字符），含 `desktop_ai_explore_20260403_main_v2_*` 会话元数据；用于 **messages 结构**审计 |
| `llm_request_20260403_121232_586be680.json` | **Plan 模式**请求（`system` 约 52381 字符），含 Plan 模式 `system-reminder` 与 `Developer: TaskDefinition` |
| 同目录下 `llm_request_20260403_121510_*.json` 等 | 后台 **记忆图编码**（`compiler_think`），非主对话，审计时勿与主对话混淆 |

### 2.4 服务端 / 运行日志

若需追踪 **图片 SSE error** 根因，请在复现时在同一时间段检索 **OpenAkita 进程标准输出 / 日志文件**（具体路径取决于 `openakita serve` 启动方式与 `LOG_*` 配置），并与 `llm_request_*` 时间戳 `20260403_1214xx` 对齐。

---

## 3. System Prompt / Messages 审计（对照规范）

### 3.1 System Prompt

- **[✅] 会话元数据**: 存在 `## 当前会话`，含会话 ID、通道、消息数等（见 `llm_request_20260403_121437_2b2a6c09.json` 内 `system`）。
- **[✅] 动态模型名**: 存在 `powered by **qwen3.5-plus**`（与 `## 系统概况` 一致）。
- **[✅] 对话上下文约定**: 存在 `## 对话上下文约定`。
- **[✅] 记忆优先级**: 存在 `## 你的记忆系统` 与三级优先级说明。
- **[✅] 无「仅供参考」**: 抽检为 **False**（未发现该字样）。
- **[✅] 工具定义**: `get_session_context` 存在；`delegate_to_agent` / `delegate_parallel` 的 schema 中含结构化字段（`delegate_parallel` 含 `tasks[].context` 等嵌套字段，详见同文件 `tools` 段）。

### 3.2 Messages 结构

- **[✅] 时间戳**: 历史轮次可见 `[HH:MM]` 前缀。
- **[✅] [最新消息]**: 最后一轮 user 可见 `[最新消息]` 前缀（抽检 `messages` 最后一条 user）。
- **[⚠️] 合并用户消息**: 某条 user 内容中**同一轮次拼接了两个不同时间戳的用户发言**（如 `[12:11]` 图片题与 `[12:12]` 技能题出现在同一条 user 消息中）。**需产品/网关确认是否为预期批处理**，否则可能影响多模态与指令边界。
- **[待人工复核] 双重时间戳**: 自动化脚本未在短前缀内稳定匹配「双重 `[HH:MM]`」模式；建议人工打开上述 JSON 对早期消息抽查。

---

## 4. 发现的问题与修复优先级

| ID | 严重程度 | 现象 | 证据位置 | 建议方向 |
|----|----------|------|----------|----------|
| P1 | **高** | 图片附件轮次出现 **`type:error` SSE**，回复为 `[SSE_ERROR: ]`，多模态链路失败 | 运行日志 Turn 17；服务端需配 vision/模型与附件解析 | 查 `chat` 路由 → Agent 多模态组装与 SSE 错误封装；确认模型是否支持 image_url；核对 `AttachmentInfo` 与 `pending_images` 分支 |
| P2 | **中** | **长文本「末尾三字」答错**（模型：**重复块**；构造文本以 `重复块结束。` 结尾，末尾汉字应为 **块结束** 相关表述，与「重复块」不符） | `tests/e2e/_ai_exploratory_comprehensive_20260403.run.log` Turn 16；`llm_request_20260403_121437_2b2a6c09.json` 中 assistant 对应内容 | 考察上下文截断、长 user 的 budget 与 prompt 提示；可加回归用例（固定字符串 + 末尾 token） |
| P2 | **中** | **首轮即混入无关项目「StarBridge Pro」**，与当前用户设定「Delta」并行，易造成用户困惑 | 运行日志 Turn 1–2 | 收紧记忆注入与「相关记忆」检索；新会话降低跨项目 bleed |
| P3 | **低** | Plan 模式仍调用 `search_memory` / `glob`（与「少工具、快出计划」理想存在张力） | 运行日志 `plan-mode` 段 | 评估 Plan 模式工具白名单或 planner 提示词 |
| P3 | **低** | `GET /api/plugins` **404**（根路径无路由） | `curl` / `urllib` 实测 | **文档与前端统一为** `GET /api/plugins/list`（已验证返回 `{"plugins":[],"failed":{}}`） |
| P4 | **低** | Windows 下脚本打印含 emoji 回复时曾 `UnicodeEncodeError` | 首次失败堆栈（已修复脚本） | 已在 `_ai_exploratory_comprehensive_20260403.py` 处理；其他 e2e 脚本可同样加 UTF-8 |

---

## 5. 对话表现小结（定性）

- **总轮次**: 主会话 20 + Plan 1 + Ask 1。
- **上下文保持**: 整体可用；存在**跨项目记忆污染**与**长文本细节错误**。
- **工具合理性**: Todo 链路合理；混淆类问题触发 `search_memory` / `search_conversation_traces` 符合规则但略重；Plan 模式有额外检索与 glob。
- **模式行为**: Ask 未调工具；Plan 产出结构化计划但工具调用偏多。

---

## 6. 后续修复计划（建议顺序）

1. **优先** 复现并修复 **图片附件 SSE error**（P1），补一条最小附件（1×1 PNG）的 e2e 或集成测试。
2. **其次** 长文本末尾字回归（P2）与 **记忆 bleed**（P2）：分离会话隔离策略与记忆注入相关性。
3. **文档** 明确插件列表 API 为 `/api/plugins/list`（P3）。
4. **可选** 审查 user 消息合并逻辑（§3.2），避免多指令与多模态抢同一轮上下文。

---

## 7. 附录：API 抽检记录

```http
GET http://127.0.0.1:18900/api/health
→ 200 {"status":"ok",...}

GET http://127.0.0.1:18900/api/skills
→ 200 （返回技能列表 JSON，体积较大）

GET http://127.0.0.1:18900/api/plugins
→ 404 Not Found

GET http://127.0.0.1:18900/api/plugins/list
→ 200 {"plugins":[],"failed":{}}
```

---

*本报告由自动化脚本 + 人工审计 JSON 生成，便于后续修复与版本对比。*
