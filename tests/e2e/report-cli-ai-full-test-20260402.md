# OpenAkita CLI AI 探索性完整测试报告

- **测试日期**: 2026-04-02
- **测试准则**: `ai-exploratory-testing.mdc`
- **运行形态**: CLI 安装态 (openakita serve)
- **服务版本**: 1.27.7+3d648e7
- **PID**: 47416
- **LLM 模型**: dashscope-deepseek-r1 / qwen3.5-plus
- **操作系统**: Windows 10 (AMD64), 默认语言环境 zh_CN cp936
- **部署模式**: bundled (PyInstaller 打包)

---

## 执行摘要

| 阶段 | 状态 | 发现问题数 |
|------|------|-----------|
| 阶段 0: 环境就绪验证 | ✅ 通过 | 1 (P2) |
| 阶段 1: 后端基础设施验证 | ⚠️ 33/35 通过 | 2 (P1) |
| 阶段 5: IM 通道代码审查 | ✅ 全部通过 | 0 |
| 阶段 6: AI 探索性对话 (23轮) | ❌ 严重问题 | 3 (P0+P1) |
| 阶段 7: 日志审计 | ❌ 严重问题 | 2 (P0+P1) |

**总计发现问题: 8 个** (P0: 1, P1: 4, P2: 3)

---

## 阶段 0: 环境就绪验证

### 0.1 健康检查 ✅

```json
GET /api/health → 200
{
  "status": "ok",
  "version": "1.27.7",
  "git_hash": "3d648e7",
  "pid": 47416,
  "agent_initialized": true,
  "local_ip": "26.26.26.1"
}
```

### 0.2 LLM 端点连通性 ✅

```json
POST /api/health/check → 200
{
  "results": [{
    "name": "dashscope-deepseek-r1",
    "status": "healthy",
    "latency_ms": 2307.0,
    "consecutive_failures": 0
  }]
}
```

### 0.3 会话系统就绪 ✅

```json
GET /api/sessions → 200
{
  "ready": true,
  "sessions": [... 32 个历史会话 ...]
}
```

### 0.4 命令注册表 ✅

```json
GET /api/commands?scope=desktop → 200
命令列表 (11 个): help, model, plan, clear, skill, persona, agent, agents, org, thinking, thinking_depth
```

**注意**: 缺少 `/status` 命令 (测试计划中提及)

### 0.5 /clear 端点 ⚠️

```json
POST /api/chat/clear {"conversation_id": "test_env_check_20260402"} → 200
{"ok": false, "error": "session not found"}
```

**问题 #P2-1**: 不存在的 conversation_id 返回 200 + `ok: false`，未返回 404。虽非 500 错误，但语义不够清晰。

---

## 阶段 1: 后端基础设施代码审查

### 验证汇总

| 编号 | 检查项 | 结果 | 文件位置 |
|------|--------|------|----------|
| 1.1 | StreamEventType 枚举 22 种 | ✅ | `src/openakita/events.py` L14-55 |
| 1.1 | TS 端 1:1 同步 | ✅ | `apps/setup-center/src/streamEvents.ts` L8-48 |
| 1.1 | 枚举类型 str,Enum | ✅ | `events.py` L14 |
| 1.2 | classify_error() | ✅ | `src/openakita/utils/errors.py` L25 |
| 1.2 | format_user_friendly_error() | ✅ | `utils/errors.py` L72 |
| 1.2 | 7 大错误分类 | ✅ | `utils/errors.py` L13-22 |
| 1.2 | CLI stream_renderer 引用 | ✅ | `cli/stream_renderer.py` L18, L232 |
| 1.2 | Gateway 引用 | ✅ | `channels/gateway.py` L43 |
| 1.3 | _pending_ui_confirms TTL | ✅ | `core/policy.py` L385, L966 |
| 1.3 | _cleanup_expired_confirms() | ✅ | `core/policy.py` L974-983 |
| 1.3 | cleanup_session() | ✅ | `core/policy.py` L985-993 |
| 1.3 | timeout/default_on_timeout | ✅ | `core/policy.py` L231-235 |
| 1.4 | Path.replace() 原子写入 | ✅ | `sessions/manager.py` L695, L700 |
| 1.4 | atomic_json_write | ✅ | `sessions/manager.py` L20, L593 |
| 1.4 | 后台保存任务追踪 | ✅ | `sessions/manager.py` L73, L94, L176 |
| **1.4** | **Cancel generation check** | **❌** | **`api/routes/chat.py` L884** |
| 1.5 | init 异常发 error+done | ✅ | `core/agent.py` L3894-3898 |
| 1.5 | reason_stream _finalize | ✅ | `core/agent.py` L4197-4211 |
| 1.5 | chat_insert finish() | ✅ | `api/routes/chat.py` L931-933 |
| **1.5** | **Pool 缺 conv_id 返回 400** | **❌** | **`api/routes/chat.py` L782** |
| 1.6 | restart_required 字段 | ✅ | `api/routes/config.py` L278-289 |
| 1.6 | hot_reloadable 字段 | ✅ | `api/routes/config.py` L282-290 |

### 失败项详情

**问题 #P1-1: Cancel 端点无 generation check**
- **位置**: `src/openakita/api/routes/chat.py` L884
- **现状**: `await get_lifecycle_manager().finish(_conv_id)` 无 generation 参数
- **对比**: `_stream_chat` finally 块 L746 使用 `finish(_conv_id, generation=busy_generation)`
- **影响**: Cancel 请求与正在进行的 stream 存在理论竞态窗口
- **建议**: 添加 generation 参数保持一致性

**问题 #P1-2: Pool 模式缺 conversation_id 未返回 400**
- **位置**: `src/openakita/api/routes/chat.py` L782
- **现状**: 空 conversation_id 被自动替换为 `api_{uuid}`
- **影响**: Pool 模式下每次请求创建新 Agent 实例，可能导致实例泄漏
- **建议**: 当 agent_pool 启用时，空 conversation_id 返回 400

---

## 阶段 5: IM 通道代码审查

| 编号 | 检查项 | 结果 | 文件位置 |
|------|--------|------|----------|
| 5.1 | add_fragment_numbers() | ✅ | `channels/text_splitter.py` L280 |
| 5.1 | 单条不加编号 | ✅ | L297: `len(chunks) <= 1: return` |
| 5.2 | _try_smart_reaction() | ✅ | `channels/gateway.py` L1423 |
| 5.2 | SMART_REACTION_ENABLED | ✅ | `gateway.py` L1430 |
| 5.2 | add_reaction 能力 | ✅ | `channels/base.py` L68, L370-386 |
| 5.3 | StreamPresenter ABC | ✅ | `channels/stream_presenter.py` L26 |
| 5.3 | start/update/finalize | ✅ | L85, L96, L113 |
| 5.3 | NullStreamPresenter | ✅ | L145 |
| 5.4 | _format_group_context 消息数 | ✅ | `gateway.py` L1412-1414 |
| 5.5 | markdown_to_plaintext 代码缩进 | ✅ | `text_splitter.py` L373-374 |
| 5.5 | 链接 URL 保留 | ✅ | `text_splitter.py` L378 |

---

## 阶段 6: AI 探索性多轮对话测试

### 测试配置

- **会话 1 ID**: `ai_test_8568a69c` (轮次 1-10)
- **会话 2 ID**: `ai_test_new_948526e1` (轮次 11-23)
- **总轮次**: 23 轮
- **总耗时**: 384 秒 (~6.4 分钟)
- **调用方式**: httpx SSE streaming (`POST /api/chat`)

### 每轮测试结果

| 轮次 | 维度 | SSE事件 | 工具调用 | done | 错误 | 回复长度 | 评价 |
|------|------|---------|---------|------|------|---------|------|
| R1 | 事实记忆(告知) | text_delta,done,heartbeat | 0 | ✅ | 0 | 445 | ❌ 乱码 |
| R2 | 事实记忆(追问) | text_delta,done,heartbeat | 0 | ✅ | 0 | 171 | ❌ 乱码 |
| R3 | 事实记忆(复述) | text_delta,done,heartbeat | 0 | ✅ | 0 | 607 | ❌ 乱码 |
| R4 | 计算 | text_delta,done,heartbeat | 0 | ✅ | 0 | 318 | ❌ 乱码 |
| R5 | 计算追问 | text_delta,done,heartbeat | 0 | ✅ | 0 | 369 | ❌ 乱码 |
| R6 | 话题跳转 | text_delta,done,heartbeat | 0 | ✅ | 0 | 342 | ❌ 乱码 |
| R7 | 话题回归 | text_delta,done,heartbeat | 0 | ✅ | 0 | 243 | ❌ 乱码 |
| R8 | 信息纠正 | text_delta,done,heartbeat | 0 | ✅ | 0 | 285 | ❌ 乱码 |
| R9 | 验证纠正 | text_delta,done,heartbeat | 0 | ✅ | 0 | 736 | ❌ 乱码 |
| R10 | /clear 测试 | text_delta,done,heartbeat | 0 | ✅ | 0 | 268 | ⚠️ clear失败 |
| R11 | 新会话 | text_delta,done,heartbeat | 0 | ✅ | 0 | 378 | ❌ 乱码 |
| R12 | 远距离回溯 | text_delta,done,heartbeat | 0 | ✅ | 0 | 569 | ❌ 乱码 |
| R13 | 交叉引用 | text_delta,done,heartbeat | 0 | ✅ | 0 | 1355 | ❌ 乱码 |
| R14 | 故意混淆 | text_delta,done,heartbeat | 0 | ✅ | 0 | 1263 | ❌ 乱码 |
| R15 | 坚持混淆 | text_delta,done,heartbeat | 0 | ✅ | 0 | 785 | ❌ 乱码 |
| R16 | 工具触发(文件) | text_delta,done,heartbeat | 0 | ✅ | 0 | 349 | ❌ 无工具 |
| R17 | 工具触发(时间) | text_delta,done,heartbeat | 0 | ✅ | 0 | 800 | ❌ 无工具 |
| R18 | 复杂表格 | text_delta,done,heartbeat | 0 | ✅ | 0 | 4636 | ⚠️ 部分可用 |
| R19 | 综合总结 | text_delta,done,heartbeat | 0 | ✅ | 0 | 920 | ❌ 乱码 |
| R20 | 压力测试 | text_delta,done,heartbeat | 0 | ✅ | 0 | 4279 | ⚠️ 部分可用 |
| R21 | 记忆纠正验证 | text_delta,done,heartbeat | 0 | ✅ | 0 | 731 | ❌ 乱码 |
| R22 | 极短输入 | text_delta,done,heartbeat | 0 | ✅ | 0 | 711 | ❌ 乱码 |
| R23 | 多语言切换 | text_delta,done,heartbeat | 0 | ✅ | 0 | 3011 | ✅ 英文正常 |

### 关键发现

#### SSE 事件分析
- **出现的事件类型**: `text_delta`, `done`, `heartbeat` (仅 3 种)
- **未出现的事件类型**: `iteration_start`, `thinking_start`, `thinking_delta`, `thinking_end`, `tool_call_start`, `tool_call_end`, `security_confirm`, `error`
- **所有轮次 done 事件正常**: 23/23 ✅
- **所有轮次 0 错误**: 23/23 ✅
- **连续解析失败**: 0 次
- **工具调用**: 0 次 (23 轮中无任何工具触发)

---

## 阶段 7: 日志审计

### 7.1 LLM Debug 日志审计

**审计文件**:
- 会话 1 主请求: `data/llm_debug/llm_request_20260402_120306_074c1a25.json` (50KB)
- 会话 2 主请求: `data/llm_debug/llm_request_20260402_120904_951a32cb.json` (83KB)
- 记忆提取请求: `data/llm_debug/llm_request_20260402_120922_65c73856.json` (4KB)

### System Prompt 审计

| 检查项 | 结果 | 详情 |
|--------|------|------|
| 会话元数据 | ✅ | `会话 ID: desktop_ai_test_8568a69c_...`, 通道: 桌面端, 类型: 私聊 |
| 动态模型名 | ✅ | `powered by **qwen3.5-plus**` (非占位符) |
| 对话上下文约定 | ✅ | 完整存在 (提问准则、记忆使用、输出格式等) |
| 记忆优先级 | ✅ | 三级: 对话历史 > 系统注入记忆 > 记忆搜索工具 |
| 无"仅供参考" | ✅ | 全文搜索无匹配 |
| 工具定义 | ❌ | `"tools": []` 空数组 — **无任何工具可用** |
| 运行模式 | ❌ | 系统提示含 `Ask 模式 — 只读` 限制 |

### Messages 结构审计

| 检查项 | 结果 | 详情 |
|--------|------|------|
| 时间戳注入 | ✅ | 历史消息带 `[12:03]` 等时间前缀 |
| [最新消息]标记 | ❌ | 未观察到最新消息标记 |
| 无双重时间戳 | ✅ | 未发现 `[HH:MM] [HH:MM]` 模式 |
| 消息顺序正确 | ✅ | user/assistant 交替正确 |
| **消息编码** | **❌❌** | **所有中文字符被替换为 `?`** |

### 消息编码问题证据

**LLM 收到的用户消息 (第一条)**:
```
"content": "????????? StarBridge ????????? Rust ??????????????? Kafka???? 5 ?????????????????????????"
```

**原始发送内容**:
```
"message": "我正在开发一个名叫 StarBridge 的项目，它是一个用 Rust 写的分布式消息队列，目标是替代 Kafka。团队有 5 人，我是技术负责人。项目已经进入了第三个迭代周期。"
```

**对比**: 所有中文字符 → `?`，英文/数字 → 保留

---

## 发现的问题清单

| # | 问题 | 严重度 | 位置/日志 | 影响范围 | 建议修复 |
|---|------|--------|----------|---------|---------|
| 1 | **中文消息编码损坏** — 通过 API 发送的中文消息在存入会话历史后，中文字符全部被替换为 `?`，LLM 无法理解用户意图 | **P0** | `data/llm_debug/llm_request_20260402_120306_*.json` messages 字段; 怀疑 `sessions/manager.py` 或 FastAPI body 解析路径存在 cp936 → UTF-8 编码冲突 | 所有中文用户的所有对话 | 排查 PyInstaller bundle 中 sys.getdefaultencoding() 和 locale 设置; 检查 session 持久化路径的文件编码; 确保 JSON body 始终以 UTF-8 解码 |
| 2 | **会话强制 Ask(只读)模式** — 通过 API 创建的会话被设置为 Ask 模式，tools 列表为空，导致所有工具调用不可用 | **P1** | `data/llm_debug/llm_request_20260402_120306_*.json` system 字段含 `<system-reminder>Ask 模式 — 只读</system-reminder>`; `"tools": []` | 所有 API 发起的对话无法使用任何工具 | 检查 `mode` 参数在 `api/routes/chat.py` 中的传递逻辑; 确保 `mode: "agent"` 覆盖默认的 Ask 模式 |
| 3 | **/clear API 对 API 创建的会话返回 session not found** | **P1** | `/api/chat/clear` 返回 `{"ok": false, "error": "session not found"}`; 原始 conv_id `ai_test_8568a69c` 被系统包装为 `desktop_ai_test_8568a69c_20260402120301_25b8b78c` | API 客户端无法清除自己创建的会话 | 在 clear 端点中支持原始 conv_id 查找，或返回包装后的实际 session_id |
| 4 | **Cancel 端点无 generation check** | **P1** | `src/openakita/api/routes/chat.py` L884: `finish(_conv_id)` 无 generation 参数 | Cancel 与 stream 存在理论竞态 | 添加 generation 参数 |
| 5 | **Pool 模式缺 conv_id 未返回 400** | **P1** | `src/openakita/api/routes/chat.py` L782: 空 conv_id 自动生成 `api_{uuid}` | Agent 实例泄漏风险 | Pool 启用时对空 conv_id 返回 400 |
| 6 | **缺少 iteration_start 等事件** — 23 轮 SSE 流中仅观察到 3 种事件类型，缺少 iteration_start, thinking_*, tool_call_* | **P2** | 所有轮次 terminal 输出日志; `tests/e2e/ai_test_results.json` | SSE 消费方无法感知思考/工具/迭代状态 | 确认 qwen3.5-plus 模型是否支持 thinking 事件; 检查事件发射条件 |
| 7 | **命令注册表缺少 /status** | **P2** | `GET /api/commands?scope=desktop` 返回 11 个命令，无 `/status` | CLI `/status` 命令可能未注册到 desktop scope | 检查 status 命令的 scope 配置 |
| 8 | **/clear 对不存在会话返回 200 非 404** | **P2** | `POST /api/chat/clear {"conversation_id": "test_env_check_20260402"}` → 200 | HTTP 语义不清晰 | 不存在的会话返回 404 |

---

## 对话表现分析

### 上下文保持: ❌ 无法评估
由于编码损坏 (#P0-1)，LLM 从未正确接收中文消息。所有 23 轮中：
- LLM 只能识别英文关键词 (StarBridge, Rust, Kafka, Flink, CAP, Markdown 等)
- LLM 反复提示"消息显示为乱码"并尝试猜测意图
- 纯英文输入 (R23) 正常响应，验证 LLM 本身无问题

### 工具使用合理性: ❌ 无法评估
由于 Ask 模式 (#P1-2)，tools 列表为空，0 次工具调用。R16 (文件创建) 中 LLM 尝试输出 XML 格式的工具调用但被当作文本处理：
```
<list_directory>
<path>.</path>
</list_directory>
<glob>
<pattern>**/test_flink_config.yaml</pattern>
</glob>
```

### 纠正响应: ❌ 无法评估
中文纠正信息无法被 LLM 识别。

### /clear 同步: ❌ 失败
`POST /api/chat/clear` 返回 `session not found`，前后端同步未生效。但清除后的新消息仍在同一会话中继续（LLM 仍记得之前的乱码交互历史）。

---

## System Prompt 审计详情

| 项目 | 状态 | 备注 |
|------|------|------|
| ✅ 会话元数据 | 通过 | session_id、通道、消息数正确 |
| ✅ 动态模型名 | 通过 | `powered by qwen3.5-plus` |
| ✅ 对话上下文约定 | 通过 | 完整且详尽 |
| ✅ 记忆优先级 | 通过 | 三级优先级正确 |
| ✅ 无"仅供参考" | 通过 | 未出现 |
| ❌ 工具定义 | 失败 | tools 数组为空 |
| ❌ Ask 模式限制 | 异常 | 不应在 agent 模式下出现 |

---

## 根因分析与修复优先级

### P0: 中文编码损坏 — 建议立即修复

**根因假设**:
1. PyInstaller 打包环境中，Python 进程的 `sys.getdefaultencoding()` 或 `locale.getpreferredencoding()` 返回 `cp936` 而非 `utf-8`
2. FastAPI/Starlette 在解析 JSON body 时，某些中间环节使用了系统默认编码而非 UTF-8
3. 会话持久化 (sessions/manager.py) 在写入/读取会话文件时可能使用了非 UTF-8 编码

**排查步骤**:
1. 在 `api/routes/chat.py` 入口打印 `request.body()` 的原始字节，确认 UTF-8 完整性
2. 检查 `sessions/manager.py` 的文件 I/O 是否显式指定 `encoding='utf-8'`
3. 在 PyInstaller spec/hook 中强制设置 `sys.setdefaultencoding('utf-8')` 或环境变量 `PYTHONUTF8=1`
4. 检查 `atomic_json_write` 是否使用 `ensure_ascii=False` + `encoding='utf-8'`

### P1: Ask 模式锁定 — 建议高优先级修复

**根因假设**:
- API `mode: "agent"` 参数可能未被正确传递到 session 创建逻辑
- 或者 Desktop 通道默认使用 Ask 模式，API 请求被识别为 Desktop 通道

**排查步骤**:
1. 检查 `chat.py` 中 `mode` 参数如何传递给 `chat_with_session_stream`
2. 确认 session 创建时 mode 的默认值
3. 确认 `<system-reminder>` Ask 模式标记的注入条件

---

## 测试数据存档

| 文件 | 路径 | 内容 |
|------|------|------|
| 对话结果 JSON | `tests/e2e/ai_test_results.json` | 23 轮完整结果 (文本、事件、工具、错误) |
| 会话 1 LLM 请求 | `data/llm_debug/llm_request_20260402_1203*.json` | 会话 1 的所有 LLM 请求 |
| 会话 2 LLM 请求 | `data/llm_debug/llm_request_20260402_1205*.json` ~ `1209*.json` | 会话 2 的所有 LLM 请求 |
| 终端完整日志 | 测试进程 PID 2608 terminal 输出 | 23 轮执行过程全记录 |

---

## 后续建议

### 立即行动 (本周)
1. **修复 P0 编码问题** — 这是 showstopper，阻断所有中文用户的正常使用
2. **修复 P1 Ask 模式问题** — 确保 API 的 mode 参数生效

### 短期 (本迭代)
3. 修复 /clear 端点的 session ID 匹配逻辑
4. 添加 cancel 端点的 generation check
5. Pool 模式下空 conv_id 返回 400

### 中期 (下迭代)
6. 确认 thinking/iteration 事件的发射条件
7. 补全 /status 命令的注册

### 回归测试
修复 P0 和 P1 后，需要**完整重新执行本测试计划** (23 轮对话 + 日志审计)，因为编码问题导致本次测试的对话质量评估全部无效。
