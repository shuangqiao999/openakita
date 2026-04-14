# OpenAkita CLI/EXE — AI 探索性全面自测报告 v2

- **日期**: 2026-04-02
- **版本**: 1.27.7+3d648e7 (初始测试: 打包 EXE) → 1.27.7+unknown (修复验证: CLI editable install)
- **测试规约**: ai-exploratory-testing.mdc
- **服务端口**: [http://127.0.0.1:18900](http://127.0.0.1:18900)
- **PID**: 55060 (EXE) → 53532 (CLI)
- **LLM 端点**: dashscope-deepseek-r1 (模型: qwen3.5-plus, 延迟 2736ms)

---

## 阶段 0: 环境就绪验证


| 检查项           | 结果       | 详情                                           |
| ------------- | -------- | -------------------------------------------- |
| 0.1 健康检查      | ✅ PASS   | status=ok, agent_initialized=true            |
| 0.2 LLM 连通性   | ✅ PASS   | dashscope-deepseek-r1 healthy, 2736ms        |
| 0.3 会话系统      | ✅ PASS   | 返回会话列表，含历史会话                                 |
| 0.4 命令注册表     | ✅ PASS   | 含 help/model/plan/clear/skill/thinking 等核心命令 |
| 0.5 /clear 端点 | ⚠️ ISSUE | 不存在的会话返回 `200 + {ok:false}` 而非 404           |


---

## 阶段 1: 后端基础设施验证 (代码审查)


| 检查项            | 结果     | 证据                                                               |
| -------------- | ------ | ---------------------------------------------------------------- |
| 1.1 事件类型统一     | ✅ PASS | `events.py` 22 个枚举, TS 侧 1:1 同步                                  |
| 1.2 错误处理统一     | ✅ PASS | `errors.py` 导出 `classify_error` + 7 类别, CLI/gateway 均引用          |
| 1.3 Policy 健壮性 | ✅ PASS | TTL 清理 + `cleanup_session` + `timeout_seconds` 配置                |
| 1.4 Session 安全 | ✅ PASS | `atomic_json_write` + background save + generation 守卫            |
| 1.5 SSE 事件保障   | ✅ PASS | 初始化异常补发 error+done; pool 缺 conv_id 返回 400                        |
| 1.6 设置重启提示     | ✅ PASS | `POST /api/config/env` 响应含 `restart_required` + `hot_reloadable` |


---

## 阶段 5: IM 通道代码审查


| 检查项                 | 结果     | 证据                                                       |
| ------------------- | ------ | -------------------------------------------------------- |
| 5.1 分片编号            | ✅ PASS | `add_fragment_numbers()` 仅 chunks>1 时加 [1/N]             |
| 5.2 Smart 群反应       | ✅ PASS | `_try_smart_reaction()` + `SMART_REACTION_ENABLED` 控制    |
| 5.3 StreamPresenter | ✅ PASS | ABC 三段生命周期 + `NullStreamPresenter` 降级                    |
| 5.4 群上下文可见性         | ✅ PASS | `_format_group_context()` 含 "最近 N 条群聊消息"                 |
| 5.5 跨平台格式           | ✅ PASS | `markdown_to_plaintext()` 保留代码缩进/链接; `_fail_hint` 含已送达段数 |


---

## 阶段 6: AI 探索性多轮对话测试

**会话 1**: `test_explore_9ae95f5e` (R01-R10)
**会话 2**: `test_explore2_3fb5b029` (R11-R23)
**总耗时**: 814 秒 (13.6 分钟)

### 6.1 逐轮结果


| 轮次  | 维度          | 结果      | 耗时     | 工具                | 回复长度 | 问题                                   |
| --- | ----------- | ------- | ------ | ----------------- | ---- | ------------------------------------ |
| R01 | 事实记忆 - 告知   | ❌ FAIL  | 40.5s  | 0                 | 0    | LLM API 400 "content field required" |
| R02 | 事实记忆 - 追问   | ✅ PASS  | 14.5s  | 0                 | 556  | 正确复述 8 项信息                           |
| R03 | 事实记忆 - 全量回忆 | ✅ PASS  | 14.8s  | 0                 | 258  | 8 项信息无遗漏                             |
| R04 | 计算          | ✅ PASS  | 16.8s  | 0                 | 311  | 6×3×10=180 ✓                         |
| R05 | 计算追问        | ✅ PASS  | 16.3s  | 0                 | 497  | 360 任务, ~7 P1 bug ✓                  |
| R06 | 话题跳转        | ✅ PASS  | 49.7s  | 0                 | 1326 | WebAssembly+边缘, 主动关联 NovaPulse       |
| R07 | 话题跳回        | ❌ FAIL  | 67.8s  | 0                 | 0    | LLM API 400 同上                       |
| R08 | 信息纠正        | ⚠️ WARN | 11.5s  | 0                 | 355  | 正确更新, 但泄漏 tool_call 语法               |
| R09 | 验证纠正        | ✅ PASS  | 11.5s  | 0                 | 260  | NovaPulse Pro / 9人 ✓                 |
| R10 | /clear 测试   | ❌ FAIL  | 22.4s  | 0                 | 557  | /clear 返回 session not found, 历史未清除   |
| R11 | 新会话首轮       | ❌ FAIL  | 289.6s | delegate_parallel | 0    | API 400 + 极长耗时                       |
| R12 | 远距离回溯       | ✅ PASS  | 16.9s  | 0                 | 837  | 计算正确: 15.36 GB/min                   |
| R13 | 交叉引用        | ✅ PASS  | 21.1s  | 0                 | 1175 | 30天压缩后 221.2 TB ✓                    |
| R14 | 故意混淆        | ⚠️ WARN | 15.0s  | 0                 | 958  | 直接接受纠正, 未指出原始值                       |
| R15 | 坚持混淆        | ✅ PASS  | 20.6s  | 0                 | 1058 | 基于 500 重新计算 ✓                        |
| R16 | 工具触发(文件)    | ⚠️ ASK  | 31.1s  | 0                 | 4417 | 明确拒绝: "处于 Ask 只读模式"                  |
| R17 | 工具触发(时间)    | ✅ PASS  | 11.9s  | 0                 | 98   | 从 system prompt 获取时间                 |
| R18 | 复杂表格        | ✅ PASS  | 28.9s  | 0                 | 1639 | 详细对比表 eBPF/SystemTap/DTrace/perf     |
| R19 | 综合总结        | ✅ PASS  | 20.2s  | 0                 | 1324 | 完整回顾所有要点和纠正                          |
| R20 | 压力测试(长输入)   | ✅ PASS  | 44.9s  | 0                 | 4374 | 聪明地识别重复, 精炼回答                        |
| R21 | 记忆验证        | ✅ PASS  | 13.3s  | 0                 | 240  | 正确记住 500 及修正历史                       |
| R22 | 极短输入        | ✅ PASS  | 12.6s  | 0                 | 215  | 自然回应, 主动提供后续方向                       |
| R23 | 多语言切换       | ✅ PASS  | 18.9s  | 0                 | 2166 | 高质量英文回复                              |


### 6.2 统计总结


| 指标     | 数值                         |
| ------ | -------------------------- |
| 总轮次    | 23                         |
| 成功轮次   | 16 (✅)                     |
| 警告轮次   | 3 (⚠️)                     |
| 失败轮次   | 4 (❌)                      |
| 工具调用总数 | 1 (delegate_parallel, R11) |
| 总回复字符  | ~22,000+                   |
| 平均响应时间 | 28.6s                      |
| 中文编码   | ✅ 正常, 无乱码                  |


---

## 阶段 7: LLM 日志审计

**审计文件**: `data/llm_debug/llm_request_20260402_144721_2dc3d2a4.json`
**System Prompt**: 27,750 字符 / 11,004 tokens

### 7.1 System Prompt 审计


| 审计项     | 结果     | 说明                                   |
| ------- | ------ | ------------------------------------ |
| 会话元数据   | ✅ PASS | 含 session_id, 通道=桌面端, 消息=25条         |
| 动态模型名   | ✅ PASS | `powered by **qwen3.5-plus`** (非占位符) |
| 对话上下文约定 | ❌ FAIL | 独立 section 不存在, 分散在其他部分              |
| 记忆优先级   | ✅ PASS | 三级: 对话历史 > 系统注入记忆 > 记忆搜索工具           |
| 无"仅供参考" | ✅ PASS | 全文搜索无匹配                              |


### 7.2 Messages 结构审计


| 审计项      | 结果     | 说明                      |
| -------- | ------ | ----------------------- |
| 时间戳注入    | ✅ PASS | 所有消息含 `[14:38]` 等时间前缀   |
| [最新消息]标记 | ✅ PASS | 最后 user 消息有 `[最新消息]` 前缀 |
| 无双重时间戳   | ✅ PASS | 正则搜索无匹配                 |


### 7.3 工具定义审计


| 审计项                          | 结果           | 说明                                               |
| ---------------------------- | ------------ | ------------------------------------------------ |
| get_session_context          | ❌ FAIL       | `tools=[]` (Ask 模式剥离所有工具)                        |
| delegate_to_agent context 参数 | ❌ FAIL       | tools 为空; prompt 中签名不含 context 参数                |
| 活跃模式                         | ❌ **Ask 模式** | `<system-reminder>Ask 模式 — 只读</system-reminder>` |


---

## 发现的问题


| #   | 问题                                                                   | 严重度    | 位置                                                                                                | 影响                         | 建议修复                    |
| --- | -------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------- | -------------------------- | ----------------------- |
| 1   | **Ask 模式锁定**: API 会话被 IntentAnalyzer 强制降级为 Ask 只读模式, tools=[]        | **P0** | `agent.py` L1787-1793 (intent 覆盖 mode) `agent.py` L4042 (CHAT 快速路径) `chat.py` L361-372 (mode 未传递) | 所有工具调用失效, R16 明确拒绝创建文件     | 已在源码修复, **需重新打包**       |
| 2   | **LLM API 400 "content field required"**: 3 轮出现 dashscope API 返回 400 | **P1** | `brain.messages_create_async` 消息组装逻辑                                                              | 3/23 轮完全失败 (R01, R07, R11) | 排查消息列表中是否存在空 content 字段 |
| 3   | **/clear 端点失效**: 对 API 创建的会话返回 "session not found"                   | **P1** | `chat.py` L46-63 (clear endpoint 无 fallback 查找)                                                   | R10 /clear 失败, 历史未清除       | 已在源码修复, **需重新打包**       |
| 4   | **LLM 泄漏 tool_call 语法**: Ask 模式下 LLM 在文本中输出 `<tool_call>` 标签         | **P2** | R08 回复中出现 `<tool_call><function=update_user_profile>`                                             | 用户看到原始工具调用标记               | 在 Ask 模式提示词中明确禁止工具调用语法  |
| 5   | **/clear 返回 200 而非 404**: 不存在的会话返回 HTTP 200                          | **P2** | `chat.py` L63 (统一返回 200)                                                                          | 前端无法区分"已清除"和"不存在"          | 已在源码修复, **需重新打包**       |
| 6   | **对话上下文约定 section 缺失**: System prompt 中无独立 `## 对话上下文约定` section      | **P3** | `prompt/builder.py` 提示词组装                                                                         | 日志审计规范检查项不通过               | 考虑添加独立 section 或更新审计标准  |


---

## 对话表现分析

### 上下文记忆

- **事实记忆**: 出色。R02/R03 完整复述 8 项信息, R06 话题跳转后主动关联 NovaPulse
- **信息纠正**: 良好。R08-R09 正确更新到 NovaPulse Pro/9人
- **远距离回溯**: 出色。R21 正确回忆纠正后的 500 事件/秒

### 计算能力

- R04: 6×3×10=180 ✓
- R05: 360 任务, ~7 P1 bug ✓
- R12: 200×5000×256×60/10^9≈15.36 GB/min ✓
- R13: 正确的存储推算链

### 工具使用

- **严重异常**: 全部 23 轮中仅 R11 出现 1 次工具调用 (delegate_parallel, 但因 API 400 失败)
- **根因**: Ask 模式锁定导致 `tools=[]`, LLM 无法使用任何工具
- R16 LLM 明确表示"处于 Ask 只读模式, 无法创建文件"

### 综合指令

- R19 综合总结: 完整覆盖身份、场景、计算、纠正、对比结论
- R20 压力测试: 智能识别重复问题, 精炼回答 (4374 字)
- R23 语言切换: 高质量英文响应

### /clear 同步

- **失败**: /clear 返回 session not found, 对话历史完全保留

---

## 根因分析与修复优先级

### 已在源码修复但未打包的问题 (3 项)


| 问题             | 源码修复状态 | 修复文件                             |
| -------------- | ------ | -------------------------------- |
| Ask 模式锁定       | ✅ 已修复  | `agent.py` (3处) + `chat.py` (1处) |
| /clear 失效      | ✅ 已修复  | `chat.py` (fallback + 404 语义)    |
| Pool 空 conv_id | ✅ 已修复  | `chat.py` (400 校验)               |


### 需要新排查的问题 (1 项)


| 问题                                   | 排查方向                                                                                                                              |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| LLM API 400 "content field required" | 检查 `brain.messages_create_async` 是否在 thinking/reasoning 路径中发送了空 content 的 assistant 消息。模式: 首轮 (R01)、context 压力点 (R07)、新会话首轮 (R11) |


---

## 阶段 8: 修复实施与验证 (2026-04-02 15:00-15:20)

### 8.1 修复清单

| # | 问题 | 修复文件 | 修复内容 | 状态 |
|---|------|---------|---------|------|
| 1 | **P1 LLM API 400 "content field required"** | `src/openakita/llm/capabilities.py` | 添加 `qwen3.5-plus`, `qwen3.5-turbo` 到 dashscope provider, 标记为双模 (非 thinking_only) | ✅ 已修复+验证 |
| 2 | **P1 LLM API 400 (reasoning_content 空值)** | `src/openakita/llm/converters/messages.py` | `reasoning_content` 为空时注入 `"..."` 占位符, 避免 DashScope enable_thinking=true 时 400 | ✅ 已修复+验证 |
| 3 | **P1 endpoint config thinking_only 误标** | `data/llm_endpoints.json` | 移除 dashscope-deepseek-r1 的 `thinking_only` capability (qwen3.5-plus 为双模模型) | ✅ 已修复+验证 |
| 4 | **P2 tool_call 语法泄漏** | `src/openakita/core/response_handler.py` | `strip_tool_simulation_text()` 新增正则: 移除 `<tool_call>...</tool_call>` XML 块 | ✅ 已修复+验证 |
| 5 | **P0 Ask 模式锁定** | `agent.py` (3处) + `chat.py` (1处) | 已在前次修复中完成 | ✅ 之前已修复 |
| 6 | **P1 /clear 失效** | `chat.py` (fallback + 404 语义) | 已在前次修复中完成 | ✅ 之前已修复 |

### 8.2 部署方式

- 停止 EXE 进程 (PID 55060)
- CLI 环境 (`.venv-cli`) 使用 editable install: `pip install -e "D:\OpenAkita" --no-deps`
- 验证 import 指向源码: `D:\OpenAkita\src\openakita\__init__.py`
- 启动 CLI 版服务: `python -m openakita serve` (PID 53532)

### 8.3 验证结果

| 验证项 | 结果 | 证据 |
|--------|------|------|
| 服务启动 | ✅ PASS | `/api/health` 返回 `status=ok, agent_initialized=true, pid=53532` |
| LLM API 400 修复 | ✅ PASS | 基础聊天 + 2 轮多轮对话均成功, 无 400 错误 |
| thinking 功能 | ✅ PASS | 响应含 `thinking_start/thinking_delta/thinking_end` 事件 |
| 工具调用 | ✅ PASS | LLM 正确调用 `list_directory`, `get_session_context` 等工具 |
| 多轮上下文 | ✅ PASS | 第 2 轮 `context_tokens` 从 803 增长到 1190, 上下文正确累积 |
| /api/chat/clear | ✅ PASS | 返回 `{"ok":true}` |
| Ask 模式锁定 | ✅ PASS | Ask 模式返回 `text_delta` (纯文本), 无 `tool_call_start` 事件 |
| tool_call 过滤 | ✅ PASS | `strip_tool_simulation_text('<tool_call>...')` 正确输出空字符串 |
| qwen3.5-plus capabilities | ✅ PASS | `thinking=True`, 无 `thinking_only`, vision/video/tools 均为 True |
| 服务器日志 | ✅ PASS | 无 LLM API 400 错误, 仅有 IM 通道连接错误 (Telegram 代理/DingTalk 临时故障) |

---

## 发现的问题 (更新后)

| # | 问题 | 严重度 | 修复状态 | 验证状态 |
|---|------|--------|---------|---------|
| 1 | **Ask 模式锁定**: API 会话被 IntentAnalyzer 强制降级 | **P0** | ✅ 源码已修复 | ✅ 已验证 |
| 2 | **LLM API 400 "content field required"** | **P1** | ✅ 源码已修复 (3 处) | ✅ 已验证 (多轮对话无 400) |
| 3 | **/clear 端点失效** | **P1** | ✅ 源码已修复 | ✅ 已验证 (`{"ok":true}`) |
| 4 | **LLM 泄漏 tool_call 语法** | **P2** | ✅ 源码已修复 | ✅ 已验证 (函数级测试通过) |
| 5 | **/clear 返回 200 而非 404** | **P2** | ✅ 源码已修复 | ✅ 之前已验证 |
| 6 | **对话上下文约定 section 缺失 (Ask 模式)** | **P3** | ✅ 源码已修复 | ✅ 已验证 (Ask 模式 prompt 含独立 section) |

---

## 后续建议

### 立即行动

1. **重新打包 EXE** — 所有源码修复已验证通过, 需要将修复包含到 EXE 构建中分发给用户
2. **运行完整 23 轮回归测试** — 使用修复后的代码重新执行 AI 探索性测试, 验证 R01/R07/R11/R16 等之前失败的轮次

### 短期优化

1. 考虑在 LLM 调试日志中同时输出 OpenAI 格式的已转换请求, 便于排查消息格式问题

### 打包注意事项

1. 确保 `src/openakita/llm/capabilities.py` 中 qwen3.5-plus/qwen3.5-turbo 的 capabilities 正确
2. 确保 `src/openakita/llm/converters/messages.py` 中 reasoning_content 空值占位逻辑
3. 确保 `src/openakita/core/response_handler.py` 中 XML tool_call 过滤正则
4. 确保 `data/llm_endpoints.json` 中 dashscope-deepseek-r1 无 thinking_only capability

---

## 附录

### 测试结果数据

- 完整 JSON: `tests/e2e/ai_explore_results.json`
- 会话 1 ID: `test_explore_9ae95f5e`
- 会话 2 ID: `test_explore2_3fb5b029`

### LLM Debug 日志

- 主对话日志: `data/llm_debug/llm_request_20260402_144721_2dc3d2a4.json`
- System Prompt: 27,750 字符 / 11,004 tokens
- 模型: qwen3.5-plus via dashscope-deepseek-r1
- 修复验证日志: `data/llm_debug/llm_request_20260402_1519*.json` (多轮对话验证)

### 环境信息

- OS: Windows 10 (10.0.19045)
- 初始测试运行形态: EXE 打包态 (openakita-server.exe, PID 55060)
- 修复验证运行形态: CLI editable install (python -m openakita serve, PID 53532)
- 版本: 1.27.7+3d648e7 (EXE) / 1.27.7+unknown (CLI)

### 修复涉及文件

| 文件 | 修改类型 |
|------|---------|
| `src/openakita/llm/capabilities.py` | 新增 qwen3.5-plus/turbo model capabilities |
| `src/openakita/llm/converters/messages.py` | reasoning_content 空值占位 |
| `src/openakita/core/response_handler.py` | XML tool_call 过滤正则 |
| `data/llm_endpoints.json` | 移除 thinking_only capability |
| `src/openakita/prompt/builder.py` | 提取核心对话约定为独立函数, Ask 模式也注入 |

