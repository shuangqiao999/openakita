# OpenAkita Dry-Run 包 AI 探索性测试报告

- **测试日期**: 2026-04-01
- **版本**: 1.27.7+8cbc023
- **部署模式**: bundled (PyInstaller)
- **LLM 端点**: dashscope-qwen3.5-plus (延迟 927ms)
- **测试地址**: http://127.0.0.1:18900/web/
- **测试方法**: 按 ai-exploratory-testing.mdc 规范执行三阶段测试

---

## 阶段 0：环境就绪验证

| 检查项 | 结果 | 详情 |
|--------|------|------|
| GET /api/health | PASS | `status: ok`, `agent_initialized: true`, PID 60324 |
| POST /api/health/check | PASS | dashscope-qwen3.5-plus: healthy, 927ms |
| GET /api/sessions | PASS | `ready: true`, 空列表 |
| Web UI (HTTP) | PASS | HTTP 200, 标题 "OpenAkita Setup Center" |
| 事件循环 | PASS | `dual_loop: true`, lag 0ms, max_concurrent 20 |

**发现**: 测试期间后端 PID 从 60324 变为 23196，表明发生过一次服务重启。

---

## 阶段 1：真实多轮对话测试（20 轮）

### 对话概况

- **会话 ID**: test_dryrun_20260401
- **总轮次**: 20
- **覆盖维度**: 事实记忆、计算追问、话题跳转、信息纠正、远距离回溯、故意混淆、综合总结

### 维度 1：事实记忆（轮次 1-3）

| 轮次 | 操作 | 结果 | 工具调用 |
|------|------|------|----------|
| 1 | 告知星辰计划项目信息 | PASS - 正确理解并存储 | add_memory (合理) |
| 2 | 追问技术栈和截止日期 | PASS - 正确回忆，计算剩余136天 | search_memory (可能不必要) |
| 3 | 要求复述所有信息 | PASS - 完整准确，含格式化表格 | add_memory (合理) |

**评估**: 事实记忆能力优秀，信息保持完整。轮次2中对刚说过的信息调用search_memory属于不必要的工具调用。

### 维度 2：计算追问（轮次 4-6）

| 轮次 | 操作 | 结果 | 工具调用 |
|------|------|------|----------|
| 4 | 计算平均工资+工作日 | PASS - 结果正确 (9124元/月, 98工作日) | run_shell + python (合理) |
| 5 | 按比例分配+对比 | PASS - 首次失败后重试成功 | write_file, run_shell x2, run_powershell (冗余) |
| 6 | 增加预算+人员重新计算 | PASS - 结果正确 | write_file, run_shell(失败), run_powershell (冗余) |

**评估**: 计算结果正确。但暴露了严重的工具调用效率问题：
- 轮次5-6中反复出现 `run_shell` 失败（路径含空格未加引号），然后回退到 `run_powershell`
- 一个计算任务需要 5 次迭代、77-117 秒才能完成
- 生成临时 Python 文件是不必要的中间步骤

### 维度 3：话题跳转（轮次 7-9）

| 轮次 | 操作 | 结果 | 工具调用 |
|------|------|------|----------|
| 7 | 突然问天空为什么是蓝色 | PASS - 正确回答瑞利散射 | 无 (正确) |
| 8 | 推荐产品经理书籍 | FAIL - 未真正回答，陷入工具使用元讨论 | 无 |
| 9 | 跳回星辰计划 | PASS - 正确回忆所有信息和tech lead | search_memory x2 |

**评估**: 话题跳转能力正常，回归也准确。但轮次 8 严重失败 —— agent 没有推荐任何书籍，而是花了整个回复讨论"什么时候该用工具"。这是 system prompt 中"工具优先"规则对知识型问答产生的副作用。

### 维度 4：信息纠正（轮次 10-12）

| 轮次 | 操作 | 结果 | 工具调用 |
|------|------|------|----------|
| 10 | 更正 tech lead 为王强，截止日期推迟 | PASS - 正确更新 | add_memory x2 (合理) |
| 11 | 验证更正是否生效 | PASS - 最终正确，但过程曲折 | search_memory, search_conversation_traces x2 |
| 12 | 新增测试成员赵丽 | PASS - 正确计算14人 | search_memory, add_memory |

**评估**: 纠正能力有效，最终结果正确。但记忆系统存在重要问题：
- `add_memory` 是追加而非替换，旧的错误记录（张伟、8月15日）仍存在
- Agent 需要通过 "已更正" 注释来区分新旧数据
- `search_conversation_traces` 持续返回空结果（与服务重启导致对话追踪丢失有关）

### 维度 5：远距离回溯（轮次 13-16）

| 轮次 | 操作 | 结果 | 工具调用 |
|------|------|------|----------|
| 13 | 追问最初的原始数据 | PASS - 正确区分原始/更新数据 | search_conversation_traces (空), search_memory |
| 14 | 追问第4轮计算细节 | PASS - 正确回忆 41,666.67 和 9,124.09 | search_conversation_traces (空) |
| 15 | 交叉引用前端lead+书籍 | PASS - 李明正确，诚实承认未推荐书 | search_conversation_traces, web_search x2 |
| 16 | 直接回忆不用工具 | PASS - PostgreSQL 正确 | 无 (遵守要求) |

**评估**: 远距离回溯能力良好，能区分原始数据和后续变更。但 `search_conversation_traces` 功能基本失效（始终返回空），agent 完全依赖对话上下文和记忆系统。

### 维度 6：故意混淆（轮次 17-19）

| 轮次 | 操作 | 结果 | 工具调用 |
|------|------|------|----------|
| 17 | 给出三个错误信息（MySQL/8人/80万）| PASS - 识别全部3个矛盾 | search_conversation_traces x3, search_memory x2, list_recent_tasks |
| 18 | 坚持80万是对的 | PASS - 坚持正确信息，用证据反驳 | 大量搜索验证 |
| 19 | 承认错误，要求自我反思 | PASS - 识别了计算精度误差 | list_recent_tasks |

**评估**: 抗混淆能力优秀，agent 未被虚假信息欺骗，能用证据支撑正确答案。但一次验证查询竟调用了 6 个工具，消耗极大。

### 维度 7：综合总结（轮次 20）

| 轮次 | 操作 | 结果 | 工具调用 |
|------|------|------|----------|
| 20 | 要求按时间线完整总结 | PASS - 生成完整报告(14KB) | search_memory, search_conversation_traces, list_recent_tasks, trace_memory, write_file, deliver_artifacts |

**评估**: 最终总结准确、结构完整，覆盖所有关键事件。但耗时 146 秒，输入 token 达 271,488。

---

## 阶段 2：日志审计

### System Prompt 审计

| 检查项 | 结果 | 详情 |
|--------|------|------|
| 会话元数据 | PASS | session_id: desktop_test_dryrun_20260401_..., 通道: 桌面端, 消息数: 29 条 |
| 动态模型名 | PASS | `powered by **qwen3.5-plus**` — 非占位符 |
| 对话上下文约定 | PASS | 完整章节存在，含时间戳注入规则和最新消息标记说明 |
| 记忆优先级 | PASS | 三级优先级正确：对话历史 > 系统注入记忆 > 记忆搜索工具 |
| 无"仅供参考" | PASS | 全文搜索未找到 |

### Messages 结构审计

| 检查项 | 结果 | 详情 |
|--------|------|------|
| 时间戳注入 | PASS | 所有历史消息带 `[HH:MM]` 前缀，如 `[16:36]`, `[16:37]` |
| [最新消息]标记 | PASS | 最后一条 user 消息包含 `[最新消息]` 标记 |
| 无双重时间戳 | PASS | 正则搜索 `\[\d{2}:\d{2}\]\s*\[\d{2}:\d{2}\]` 无匹配 |

### 工具定义审计

| 检查项 | 结果 | 详情 |
|--------|------|------|
| get_session_context | PASS | 工具存在 |
| delegate_to_agent | PASS | 工具存在 |
| delegate_parallel | PASS | 工具存在 |
| context 参数 | PASS | 在工具定义中找到 |

### 日志审计补充发现

- System Prompt 长度约 177KB，包含完整的 SOUL.md、角色设定、86 个系统技能 + 69 个外部技能索引、MCP 服务器配置、21 个 Agent 团队成员
- 单次请求 system prompt 消耗约 58,000+ token
- 记忆系统注入了大量冗余的重复经验记录（"工具优先与真实落地"协议出现了 5+ 次）

---

## 阶段 3：Web UI 功能快测

| 检查项 | 结果 |
|--------|------|
| /web/ 页面加载 | PASS - HTTP 200, HTML 正确渲染 |
| 页面标题 | PASS - "OpenAkita" |
| 启动内容 | PASS - "OpenAkita Setup Center" |

---

## 发现的问题

### P0 - Critical

| # | 问题 | 严重程度 | 建议修复方案 |
|---|------|----------|-------------|
| 1 | **服务重启导致 conversation_traces 全部丢失** | Critical | PID 从 60324 变为 23196 后，`search_conversation_traces` 持续返回空。对话追踪应持久化到磁盘（SQLite），而非仅驻留内存。 |

### P1 - Major

| # | 问题 | 严重程度 | 建议修复方案 |
|---|------|----------|-------------|
| 2 | **记忆系统 add_memory 不替换旧记录** | Major | 更正事实后旧数据（张伟/8月15日）仍存在，可能导致混淆。建议实现 upsert 语义或自动标记旧记录为 superseded。 |
| 3 | **轮次 8 未回答用户问题** | Major | Agent 完全跳过了"推荐3本书"的请求，转而讨论工具使用边界。system prompt 中"工具优先"规则对知识型问答产生了负面干扰。建议在消息分型原则中强化"问答型请求直接用知识回答"。 |
| 4 | **run_shell 路径含空格执行失败** | Major | `D:\Program Files\OpenAkita\...` 路径在 cmd.exe 中因空格被截断。Agent 每次都需要 fallback 到 run_powershell，浪费一次迭代。建议在 run_shell 工具中自动为包含空格的路径加引号。 |
| 5 | **记忆系统注入大量冗余经验** | Major | "工具优先与真实落地"协议在核心记忆中重复出现 5+ 次，消耗了宝贵的 context token。建议记忆合并/去重机制更积极地清理。 |

### P2 - Minor

| # | 问题 | 严重程度 | 建议修复方案 |
|---|------|----------|-------------|
| 6 | **工具调用过度** | Minor | 简单回忆类问题（如轮次 2 追问刚说过的信息）也调用 search_memory；验证类问题调用 5-6 个工具。建议在 system prompt 中加强"对话历史中存在的信息直接引用"的规则权重。 |
| 7 | **Agent 过度元评论** | Minor | 多个轮次中 agent 添加大量"何时该用工具"的说明性文字，占用回复篇幅。这不是用户请求的内容。建议在 system prompt 消息分型原则中加入"不要向用户解释工具使用决策过程"。 |
| 8 | **计算方法不一致** | Minor | 不同轮次使用了不同的"月数"计算方式（总天数/30 vs 工作日/21.75），导致数字存在约 1-2% 偏差。建议统一计算口径。 |
| 9 | **响应时间偏长** | Minor | 复杂问题耗时 60-146 秒，简单问题也需 20-35 秒。大部分时间消耗在多次 LLM 推理迭代上。 |
| 10 | **Token 消耗极高** | Minor | System Prompt 约 58K token，20 轮对话后单次请求最高达 367K input token，接近 186K 的 context limit。建议优化 system prompt 长度和记忆注入量。 |

---

## 改进建议

### 功能改进

1. **记忆系统 Upsert**: 实现 `update_memory` 或在 `add_memory` 中支持 `supersedes_id` 参数，自动废弃被更正的旧记录
2. **对话追踪持久化**: 确保 `search_conversation_traces` 的数据在服务重启后不丢失
3. **消息分型强化**: 在 system prompt 中明确知识型问答（书籍推荐、科普解释）无需调用工具，直接用知识回答

### 性能改进

4. **System Prompt 瘦身**: 当前 ~58K token 过长，可将技能索引、Agent 团队列表等按需加载而非全量注入
5. **记忆去重**: 清理重复的经验记录，减少 token 浪费
6. **迭代控制**: 简单问答限制为 1-2 次迭代，避免 5+ 次迭代的资源浪费
7. **run_shell 路径处理**: 自动为 Windows 路径加引号，避免空格导致的失败重试

### 用户体验改进

8. **减少元评论**: Agent 不应向用户解释"为什么不用工具"或"工具使用边界"，直接回答即可
9. **统一计算口径**: 涉及日期/金额计算时，明确标注使用的算法和近似值
10. **响应速度**: 优化迭代策略，目标将简单问答控制在 10 秒内、复杂任务 60 秒内

---

## 测试环境信息

- **测试执行者**: Cursor AI Agent (Claude)
- **后端进程**: openakita-server.exe (PID 23196 at end of test)
- **日志目录**: `C:\Users\Peilong_Hong\.openakita\workspaces\default\data\llm_debug\`
- **日志文件时间范围**: 20260401_163656 - 20260401_190941
- **会话通道**: desktop (API via curl.exe)
