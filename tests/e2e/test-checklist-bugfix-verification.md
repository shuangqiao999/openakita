# 修复验证测试清单

> 覆盖范围：组织编排修复、内存去重修复、UX 优化、安全策略修复、Python 3.9 TimeoutError 全局兼容性修复
> 涉及修改文件：~40 个

---

## 一、组织编排（orgs）

### 1.1 子链委派与 gather 汇总

| # | 测试项 | 验证方法 | 预期结果 | 涉及文件 |
|---|--------|----------|----------|----------|
| 1 | 单次委派：tech-lead 向 fe-lead 委派一个任务 | 发送 "请让前端组写一个登录页面" | fe-lead 收到任务并执行，结果通过 `_auto_send_result` 回传给 tech-lead；tech-lead 自动汇总 | `tool_handler.py`, `runtime.py` |
| 2 | 并行委派：tech-lead 同时向 fe-lead 和 be-lead 委派 | 发送 "请让前端组做登录页、后端组做认证接口" | 两个子链并行执行，gather 等待两者完成后生成汇总报告 | `runtime.py:_gather_children` |
| 3 | 子链超时处理 | 设置节点 `timeout_s=10`，委派一个耗时 >10s 的任务 | 超时节点状态变为 IDLE（非 ERROR/FROZEN），chain 标记为 "timeout"，不触发熔断器 | `runtime.py:_NodeTimeoutError` |
| 4 | gather 中单个子链超时 | 并行委派 2 个任务，其中 1 个节点 timeout | 超时的子链标记为 timeout，另一个正常完成；汇总报告中显示 "1/2 已完成" 并说明超时情况 | `runtime.py:_gather_children` |
| 5 | 链追踪内存清理 | 执行多轮委派-gather 后检查 `_child_chains` 字典 | gather 完成后 `_child_chains`, `_chain_completion_events`, `_chain_results` 中对应条目被清理 | `runtime.py:_cleanup_chain_tracking` |

### 1.2 看门狗与熔断器

| # | 测试项 | 验证方法 | 预期结果 |
|---|--------|----------|----------|
| 6 | 看门狗阈值：600s | 让一个节点处于 BUSY 超过 600s | 看门狗检测到 stuck 节点并发出告警/重置 |
| 7 | clone 节点覆盖 | 创建一个 clone 节点并让其卡死 | 看门狗同样检测 clone 节点（不再跳过 `is_clone`） |
| 8 | 熔断器：连续失败冻结 | 让同一节点连续失败 N 次（达到 `_CIRCUIT_BREAKER_THRESHOLD`） | 节点状态变为 FROZEN，自动安排解冻 |
| 9 | 超时不触发熔断 | 节点因超时而非异常结束 | `_node_consecutive_failures` 不递增，不触发 FROZEN |

### 1.3 消息与通信

| # | 测试项 | 验证方法 | 预期结果 |
|---|--------|----------|----------|
| 10 | `_auto_send_result` 自动回传 | 子节点完成任务后检查 messenger 消息 | 子节点自动向父节点发送 TASK_RESULT 消息 |
| 11 | WebSocket 事件广播 | 监听 ws 事件 | 应收到 `org:gather_started`, `org:gather_complete`, `org:node_status` 等事件 |

---

## 二、内存系统（memory）

### 2.1 写入去重

| # | 测试项 | 验证方法 | 预期结果 | 涉及文件 |
|---|--------|----------|----------|----------|
| 12 | `save_semantic` FTS5 去重 | 连续两次保存相同内容的 SemanticMemory | 第二次返回已有 memory 的 ID，不写入 SQLite | `unified_store.py:_check_semantic_duplicate` |
| 13 | `save_semantic` scope 隔离 | 保存相同内容但不同 scope 的两条记忆 | 两条都成功保存（scope 不同不算重复） | `unified_store.py` |
| 14 | `manager.add_memory` FTS5 模式去重 | 在无 vector_store 的情况下，连续添加相似记忆 | 第二次被 FTS5 搜索去重拦截，返回空字符串 | `manager.py:add_memory` |
| 15 | `skip_dedup=True` 旁路 | `add_memory` 路径通过 `skip_dedup=True` 调用 `save_semantic` | manager 自身已去重，`save_semantic` 不再重复检查 | `manager.py`, `unified_store.py` |

### 2.2 `_memories` 一致性

| # | 测试项 | 验证方法 | 预期结果 | 涉及文件 |
|---|--------|----------|----------|----------|
| 16 | 去重命中时 `_memories` 不添加幽灵条目 | 通过 `_save_extracted_item` 保存一条已存在的记忆 | `save_semantic` 返回 `dup_id`（!= `mem.id`），`_memories` 中**不包含**新的 `mem.id` | `manager.py:548` |
| 17 | 压缩提取去重一致性 | `on_context_compressing` 提取与已有记忆相同的 fact | 该 fact 不被加入 `_memories` | `manager.py:893` |
| 18 | 正常保存时 `_memories` 同步 | 保存一条全新记忆 | `saved_id == mem.id`，`_memories[mem.id]` 存在 | `manager.py` |

---

## 三、意图分析与 Plan 建议

| # | 测试项 | 验证方法 | 预期结果 | 涉及文件 |
|---|--------|----------|----------|----------|
| 19 | 简单任务不建议 Plan | 发送 "帮我查一下天气" | `score < 2` → `suppress_plan=True`，不出现任何 plan 建议 | `intent_analyzer.py` |
| 20 | 复杂任务建议 Plan（score>=5） | 发送 "重构整个认证模块，改为 OAuth2，涉及前后端" | `score >= 5` → `suggest_plan=True`，消息末尾附加软建议提示 | `intent_analyzer.py`, `agent.py:4498` |
| 21 | LLM 标记 + 中等复杂度（score>=3） | LLM 返回 `suggest_plan=true` 且 `score=3` | `llm_flag=True, score>=max(5-2,2)=3` → `suggest_plan=True` | `intent_analyzer.py:59` |
| 22 | `suppress_plan` 阻止软建议 | `score < 2` 的简单任务 | 即使其他条件满足，`suppress_plan=True` 阻止 agent.py 的软建议逻辑 | `agent.py:4506` |
| 23 | 阈值可配置 | 设置 `plan_suggest_threshold=3` | 复杂度 score=3 即触发 plan 建议 | `config.py` |

---

## 四、会话消息去重

| # | 测试项 | 验证方法 | 预期结果 | 涉及文件 |
|---|--------|----------|----------|----------|
| 24 | 相邻重复消息去重 | 连续两次 `add_message("user", "你好")` | 第二次被丢弃，`messages` 长度不变 | `session.py:91-94` |
| 25 | 滑动窗口去重（window=8） | 在 8 条消息内插入重复 | 被窗口内 md5 去重拦截 | `session.py:96-108` |
| 26 | 超出窗口的合法重复 | 中间间隔 >8 条消息后再次说 "好的" | 不被去重，正常添加 | `session.py` |
| 27 | agent.py 历史去重（window=6） | 在 `_prepare_session_context` 中传入含重复的 history | 重复消息被移除，日志打印移除数量 | `agent.py:3341-3366` |

---

## 五、安全策略

| # | 测试项 | 验证方法 | 预期结果 | 涉及文件 |
|---|--------|----------|----------|----------|
| 28 | `require_confirmation` 标志生效 | 配置一条 `require_confirmation=true` 的工具策略规则 | 调用该工具时触发确认流程 | `policy.py` |
| 29 | 高危 shell 命令检测 | 执行 `rm -rf /etc/` 或写入系统目录的命令 | 被 `_HIGH_RISK_SHELL_PATTERNS` 匹配，标记为高风险 | `policy.py` |

---

## 六、Python 3.9 TimeoutError 兼容性（全局，49 处）

> 核心验证：所有 `asyncio.wait_for()` 超时场景在 Python 3.9 上能被正确捕获

### 6.1 P0 — 控制流关键路径

| # | 测试项 | 触发方式 | 预期结果 | 涉及文件 |
|---|--------|----------|----------|----------|
| 30 | LLM 冷静期恢复 | 所有 LLM 端点进入冷静期，等待恢复 | 等待超时后正常继续降级逻辑，不抛异常 | `llm/client.py:875` |
| 31 | LLM 重试退避 | 触发 429/503 错误 + cancel_event 退避 | 退避正常 pass，进入下一轮重试 | `llm/client.py:1230` |
| 32 | LLM 流式退避 | 流式请求遇到 429，退避等待 | 退避结束后不误标端点 `mark_unhealthy` | `llm/client.py:712` |
| 33 | 终端命令转后台 | 执行一个耗时较长的 shell 命令 | 命令超时后转为后台任务，返回 ShellResult（非异常） | `tools/terminal.py:268` |
| 34 | 浏览器锁等待超时 | 多个浏览器工具并发竞争锁 | 超时返回友好错误提示，不中断工具流程 | `tools/handlers/browser.py:147` |
| 35 | 工具并行执行超时 | 多个工具并行执行，总时间超限 | 超时后仍返回已完成工具的结果 | `agents/backends.py:123`, `streaming_tool_executor.py:152` |
| 36 | 企微认证超时 | WebSocket 连接后认证等待超过 10s | 正确取消 receive_task，抛出 ConnectionError 触发重连 | `wework_ws.py:762` |

### 6.2 P1 — 副作用修正

| # | 测试项 | 触发方式 | 预期结果 | 涉及文件 |
|---|--------|----------|----------|----------|
| 37 | WebSocket 保活 | 客户端 30s 内不发消息 | 服务端发送 ping，连接保持（不 break 退出） | `websocket.py:163` |
| 38 | 企微 ack 清理 | 回复消息等待 ack 超时 | `_pending_acks[req_id]` 被正确 pop，抛出带 req_id 的 TimeoutError | `wework_ws.py:2021,2226` |
| 39 | Shell 命令超时杀进程 | `run_shell` 执行超时 | `_kill_process_tree` 被调用，子进程被终止，返回超时专用 CommandResult | `tools/shell.py:481` |
| 40 | PowerShell 超时杀进程 | PowerShell 命令超时 | 子进程被 kill | `powershell.py:281` |
| 41 | CLI 命令超时杀进程 | `cli_anything` 命令超时 | `proc.kill()` 执行 | `cli_anything.py:64` |
| 42 | LLM farewell 超时不 reset cooldown | 停止任务时 farewell LLM 调用超时 | 仅打印 warning，不调用 `_reset_structural_cooldown_after_farewell()` | `reasoning_engine.py:4177` |
| 43 | 应用关停超时日志 | 关停应用时 IM channel drain 超时 | 打印 "Shutdown timeout, forcing exit" warning | `main.py:2146` |

### 6.3 P2 — 日志与消息准确性

| # | 测试项 | 触发方式 | 预期结果 | 涉及文件 |
|---|--------|----------|----------|----------|
| 44 | 健康检查超时分类 | LLM 端点健康检查超时 | 返回 "error: timeout (15s)" 而非泛化错误 | `llm/client.py:299` |
| 45 | 定时任务超时文案 | 定时任务执行超时 | 返回中文 "任务执行超时（超过 N 分钟）" | `scheduler/executor.py:387` |
| 46 | 系统任务超时文案 | 系统维护任务超时 | 返回 "System task X timed out after Ys" | `scheduler/executor.py:604` |
| 47 | OneBot API 超时异常类型 | OneBot API 调用超时 | 抛出 RuntimeError("API call timeout: action") | `onebot.py:590` |
| 48 | 企微消息处理超时文案 | 消息处理超时 | 用户收到 "处理超时，请重新发送" 而非 "处理出错" | `wework_ws.py:911` |
| 49 | ruff/eslint 超时返回值 | 代码质量检查超时 | 返回 "[ruff] Timed out after 30s" 而非 None | `code_quality.py:117,175` |
| 50 | 插件超时分类 | 插件执行超时 | 走 timeout 专用分类，`error_tracker.record_error(..., "timeout")` | `plugins/sandbox.py:81` |
| 51 | Playwright 启动超时 | 浏览器驱动启动超时 | 记录 "启动超时" 而非泛化启动失败 | `tools/browser/manager.py:535` |

### 6.4 额外发现的文件（计划外）

| # | 测试项 | 涉及文件 | 验证要点 |
|---|--------|----------|----------|
| 52 | Gateway 超时处理（5 处） | `channels/gateway.py` | 各超时路径均走正确的超时分支 |
| 53 | Chat 路由超时（2 处） | `api/routes/chat.py` | 聊天 API 超时返回正确错误 |
| 54 | 健康检查路由超时 | `api/routes/health.py` | 健康检查端点超时正确分类 |
| 55 | 组织 API 超时 | `api/routes/orgs.py` | 组织操作超时正确处理 |
| 56 | LSP 反馈超时 | `core/lsp_feedback.py` | LSP 进程超时正确处理 |
| 57 | 沙箱执行超时 | `core/sandbox.py` | 沙箱命令超时正确处理 |
| 58 | 插件钩子超时 | `plugins/hooks.py` | 插件钩子超时走正确分支 |
| 59 | 插件管理器超时 | `plugins/manager.py` | 插件加载超时正确处理 |
| 60 | OpenCLI 超时 | `tools/handlers/opencli.py` | CLI 命令超时正确处理 |
| 61 | AgentOrchestrator 超时（2 处） | `agents/orchestrator.py` | 多 Agent 编排超时正确处理 |
| 62 | MCP 工具超时（3 处） | `tools/mcp.py` | MCP 服务器调用超时正确处理 |
| 63 | Messenger 超时 | `orgs/messenger.py` | 组织消息发送超时正确处理 |
| 64 | 测试 Runner 超时 | `testing/runner.py` | 测试执行超时正确处理 |
| 65 | Memory Manager 超时 | `memory/manager.py` | 记忆提取超时正确处理 |

---

## 七、Prompt 与上下文优化

| # | 测试项 | 验证方法 | 预期结果 | 涉及文件 |
|---|--------|----------|----------|----------|
| 66 | 静态 prompt 缓存 | 多轮对话中检查 prompt builder 调用 | 静态部分从缓存返回，不重复构建 | `builder.py` |
| 67 | 语言适配 | 设置 `session.config.language="zh-CN"` | system prompt 包含语言指令 | `builder.py` |
| 68 | 技能渐进披露 | 第一轮对话不提及技能关键词 | skill_catalog 仅显示摘要；提及关键词后显示完整 | `builder.py` |

---

## 八、回归测试要点

| # | 测试项 | 验证方法 | 预期结果 |
|---|--------|----------|----------|
| 69 | Python 3.11+ 行为不变 | 在 Python 3.11+ 环境运行全部测试 | `(asyncio.TimeoutError, TimeoutError)` 中两者是同一类，行为与修改前完全一致 |
| 70 | 正常对话流程 | 进行完整的多轮对话 | 不出现异常中断、重复消息、幽灵记忆 |
| 71 | 组织编排完整流程 | 创建组织 -> 启动 -> 发送任务 -> 委派 -> 汇总 -> 停止 | 全流程正常，无 BUSY 节点残留 |
| 72 | LLM 多端点降级 | 主端点不可用时 | 自动降级到备用端点，不误标健康端点 |
| 73 | 长时间运行稳定性 | 连续运行 >1h | 无内存泄漏（chain tracking 清理）、无子进程残留 |

---

## 执行说明

- **环境要求**：建议在 Python 3.9 和 Python 3.11+ 两个环境下各运行一次
- **P0 测试项**（#30-36）为最高优先级，必须全部通过
- **组织编排测试**（#1-11）需要完整的组织配置和多节点环境
- **内存测试**（#12-18）可通过单元测试验证
- **TimeoutError 测试**最直接的验证方式：在 Python 3.9 环境中，对 `asyncio.wait_for` 设置极短 timeout（如 0.001s）触发超时，确认走入正确的 except 分支
