# OpenAkita AI 探索性全面自测报告 v3

- **日期**: 2026-04-02 17:47 — 18:25
- **版本**: 1.27.7+unknown (CLI editable install, pip install -e)
- **测试规约**: ai-exploratory-testing.mdc
- **服务端口**: http://127.0.0.1:18900
- **PID**: 29916
- **LLM 模型**: qwen3.5-plus (via dashscope)
- **前端**: http://localhost:5173/web/ (HTTP 200)

---

## 阶段 0: 环境就绪验证

| 检查项 | 结果 | 详情 |
|--------|------|------|
| 0.1 健康检查 | ✅ PASS | status=ok, agent_initialized=true, pid=29916 |
| 0.2 前端可达 | ✅ PASS | HTTP 200 |
| 0.3 会话系统 | ✅ PASS | `/api/sessions` 返回历史会话列表 |
| 0.4 命令注册表 | ✅ PASS | 11 个命令: help/model/plan/clear/skill/persona/agent/agents/org/thinking/thinking_depth |
| 0.5 /clear 对不存在会话 | ✅ PASS | 返回 HTTP 404（之前版本返回 200，已修复） |

---

## 阶段 1: AI 探索性多轮对话测试

**会话 ID**: `v3b_20260402`
**总轮次**: 23
**总耗时**: ~752 秒 (12.5 分钟)

### 1.1 逐轮结果

| 轮次 | 维度 | 结果 | 耗时 | 工具调用 | 回复长度 | 问题 |
|------|------|------|------|----------|----------|------|
| R01 | 事实记忆·告知 | ⚠️ WARN | 85.5s | update_user_profile×14, add_memory×6 | 455 | 正确，但**18次工具调用**过度 |
| R02 | 事实记忆·架构追问 | ❌ FAIL | >250s | delegate_to_agent (循环) | 0 | **P0: 委派死循环** |
| R03 | 工具触发·列目录 | ❌ FAIL | 17.2s | list_directory×5 | 34 | **工具循环**: list_directory 反复调用 |
| R04 | 计算·简单数学 | ✅ PASS | 14.6s | 0 | 85 | 1+1=2 ✓，主动关联 SkyForge |
| R05 | 事实回忆 | ✅ PASS | 12.9s | 0 | 171 | 正确回忆名字/项目/技术栈 |
| R06 | 话题跳转·Rust vs Go | ✅ PASS | 44.9s | web_search×1 | 236 | 正确，但不必要调用 web_search |
| R07 | 话题跳回 | ✅ PASS | 17.6s | 0 | 393 | 正确回忆12人/北京深圳/200万 |
| R08 | 信息纠正 | ✅ PASS | 34.3s | update_user_profile, add_memory | 375 | 完美更新，**无 XML 泄漏** ✅ |
| R09 | 验证纠正 | ✅ PASS | 82.1s | add_memory, search_memory | 136 | 正确，但 82s 过慢 |
| R10 | 故意混淆·虚假纠正 | ⚠️ WARN | 18.2s | 0 | 675 | 直接接受 Java 纠正，**未发现矛盾** |
| R11 | 挑战混淆 | ✅ PASS | 25.9s | 0 | 540 | 承认矛盾，正确恢复 Go 技术栈 |
| R12 | 计算·QPS | ✅ PASS | 21.6s | 0 | 730 | 总10000 QPS，合并后4000 ✓ |
| R13 | 计算追问·Kafka | ✅ PASS | 22.4s | 0 | 897 | 4000条/秒，3.46亿条/天 ✓ |
| R14 | 远距离回溯+重新计算 | ✅ PASS | 59.0s | run_shell×1 | 1012 | 10000 QPS，8.64亿条/天 ✓ |
| R15 | 时间查询 | ✅ PASS | 37.7s | run_skill_script×2 | 79 | 2026年4月2日星期四 ✓ |
| R16 | 综合总结 | ✅ PASS | 72.7s | get_session_context, add_memory×2 | 2321 | 完美结构化总结 |
| R17 | 压力测试·15项排序 | ❌ FAIL | >250s | delegate (循环) | 0 | **P0: 委派死循环**（与R02相同） |
| R18 | 极短输入·"嗯" | ⚠️ WARN | 47.9s | get_session_context | 3325 | 输出了R17的结果（上下文残留） |
| R19 | 极短输入·"ok" | ✅ PASS | 12.6s | 0 | 209 | 自然回应 |
| R20 | 多语言切换·英文 | ✅ PASS | 26.8s | 0 | 2919 | 高质量英文总结 |
| R21 | /clear 测试 | ✅ PASS | - | - | - | HTTP 200 `{"ok":true}` |
| R22 | /clear 后查询 | ❌ FAIL | 47.7s | get_user_profile×9, search_memory×2 | 34 | **工具循环**: 反复查询已清空的 profile |
| R23 | /clear 后问候 | ✅ PASS | 43.4s | get_user_profile | 104 | 确认上下文已清空 |

### 1.2 统计总结

| 指标 | 数值 |
|------|------|
| 总轮次 | 23 |
| 成功轮次 | 14 (✅) |
| 警告轮次 | 3 (⚠️) |
| 失败轮次 | 4 (❌) |
| 超时/死循环轮次 | 2 (R02, R17) |
| 工具循环轮次 | 2 (R03, R22) |
| 总工具调用 | ~80+ (含循环) |
| 平均响应时间(成功轮次) | 34.0s |
| 中位数响应时间(成功轮次) | 25.9s |
| 总回复字符(成功轮次) | ~14,000+ |
| Thinking 功能 | ✅ 正常工作 (所有轮次均有 thinking_len>0) |
| 中文编码 | ✅ 正常，无乱码 |

---

## 阶段 2: LLM 日志审计

**审计主文件**: `data/llm_debug/llm_request_20260402_182032_e310690b.json`
**System Prompt**: 60,419 字符
**Messages**: 43 条（含大量工具调用/结果消息）
**Tools**: 86 个

### 2.1 System Prompt 审计

| 审计项 | 结果 | 说明 |
|--------|------|------|
| 会话元数据 (session_id) | ✅ PASS | 含 session_id、通道信息 |
| 动态模型名 | ✅ PASS | `powered by **qwen3.5-plus**` |
| 对话上下文约定 | ✅ PASS | 独立 section 存在 |
| 记忆优先级 | ✅ PASS | 三级: 对话历史 > 系统注入记忆 > 记忆搜索工具 |
| 无"仅供参考" | ✅ PASS | 全文搜索无匹配 |
| 协作优先原则 | ⚠️ 注意 | "当委派与自己执行冲突时，选择委派" — **这是委派死循环的根因之一** |

### 2.2 Messages 结构审计

| 审计项 | 结果 | 说明 |
|--------|------|------|
| 时间戳注入 | ✅ PASS | 所有用户消息含 `[HH:MM]` 时间前缀 |
| [最新消息]标记 | ✅ PASS | 最后一条 user 消息有 `[最新消息]` 前缀 |
| 无双重时间戳 | ✅ PASS | 正则搜索无 `[HH:MM] [HH:MM]` 匹配 |
| 消息顺序 | ✅ PASS | user/assistant 交替正确 |

### 2.3 工具定义审计

| 审计项 | 结果 | 说明 |
|--------|------|------|
| get_session_context | ✅ EXISTS | 正常注册 |
| delegate_to_agent | ✅ EXISTS | 正常注册 |
| delegate_parallel | ✅ EXISTS | 正常注册 |
| 工具总数 | ⚠️ 86 个 | 数量过多，包含 browser_*×8, desktop_*×6, MCP 相关×6 等 |

### 2.4 委派死循环日志分析

**日志文件**: `data/llm_debug/llm_request_20260402_175617_70c00af9.json`

**循环路径**:
```
用户发送架构问题
  → LLM 回复：文本回答架构分析（无工具调用）
  → Supervisor 注入："[系统] ⚠️ 你的上一条回复没有调用任何工具"
  → LLM 被迫调用 delegate_to_agent（委派给架构师 Agent）
  → 系统检测到循环："⚠️ 检测到工具调用陷入死循环"
  → LLM 再次尝试委派
  → 无限循环直到超时
```

**根因链**:
1. System prompt 含 "协作优先原则：当委派与自己执行冲突时，选择委派"
2. `reasoning_engine.py:3916-3928` — 无 intent tag 时强制要求工具调用
3. LLM 本来给出了正确的文本回答，但被 supervisor 否决
4. LLM 被迫选择 delegate_to_agent 作为"工具调用"
5. 委派目标检测为循环，返回错误，但 supervisor 继续强制
6. 形成不可退出的循环

**日志位置**:
- Supervisor 强制工具调用逻辑: `src/openakita/core/reasoning_engine.py` L3890-L3928
- 循环检测逻辑: 同文件 L3937 之后
- 委派优先原则: System prompt `## 协作优先原则（最高优先级）`

---

## 发现的问题

| # | 问题 | 严重度 | 位置 | 影响 | 建议修复 |
|---|------|--------|------|------|----------|
| 1 | **Supervisor 强制工具调用导致委派死循环**: 纯文本回答被 supervisor 否决，强制 LLM 调用工具，LLM 选择 delegate_to_agent → 循环检测 → 再次强制 → 无限循环 | **P0** | `reasoning_engine.py` L3890-3928 (ForceToolCall 逻辑); System prompt "协作优先原则" | R02/R17 完全失败(>250s 超时), R03/R22 工具循环 | **方案A**: 在 no-intent-tag 分支中，如果 LLM 给出了有效文本回复(stripped_text非空)，直接返回文本而不强制工具调用; **方案B**: 识别"知识/咨询"类 intent，豁免 ForceToolCall |
| 2 | **R01 过度工具调用**: 一条自我介绍消息触发 18 次工具调用 (update_user_profile×14, add_memory×6)，耗时 85.5s | **P2** | Agent 工具调用策略; `update_user_profile` 和 `add_memory` 被重复调用 | 响应时间从预期 15s 膨胀到 85s | 限制单轮 update_user_profile 调用次数，或合并多个 profile 更新为单次调用 |
| 3 | **不必要的工具调用**: R06(web_search), R14(run_shell), R15(run_skill_script×2) 均为纯知识/计算问题，不需要工具 | **P2** | System prompt 工具选择优先级; supervisor ForceToolCall 压力 | 响应时间增加 20-40s | 在 system prompt 中明确"纯知识问答、数学计算、架构讨论等不需要调用工具" |
| 4 | **工具总数过多(86个)**: 包含 browser_*×8, desktop_*×6 等桌面/浏览器工具，API 聊天场景不需要 | **P2** | 工具注册逻辑; `server.py` 或 `agent.py` 工具加载 | 增加 LLM token 消耗(system prompt 60KB)，增加选择困难 | 按通道/模式动态裁剪工具集，API 聊天不注入 browser/desktop 工具 |
| 5 | **故意混淆测试未主动质疑**: R10 中用户声称技术栈从 Go 改为 Java，系统未发现与 R06(Rust vs Go 讨论)的矛盾 | **P3** | LLM 行为层面 | 用户可能被误导 | 可在 system prompt 中添加"当用户修改之前的关键事实时，先确认矛盾再更新" |
| 6 | **平均响应时间偏长**: 成功轮次平均 34s，中位数 25.9s，部分轮次 >60s | **P3** | 工具调用开销; supervisor 重试; 86 个工具的 token 占用 | 用户体验不佳 | 减少工具数量、优化 supervisor 逻辑、减少不必要的工具调用 |

---

## 对话表现分析

### 上下文记忆
- **事实记忆**: 出色。R03/R05/R07 完整回忆所有项目信息
- **信息纠正**: 优秀。R08 完美更新 4 项信息，R09 验证通过
- **远距离回溯**: 出色。R14 在 14 轮后正确引用并重新计算
- **综合总结**: 出色。R16 完整回顾所有要点、决策和数字

### 计算能力
- R04: 1+1=2 ✓
- R12: 5×2000=10000, 合并后 4000 ✓
- R13: 4000×86400=3.46亿 ✓
- R14: 10000×86400=8.64亿 ✓

### 工具使用
- **严重问题**: ForceToolCall 机制导致不必要的工具调用和死循环
- R01: 18次工具调用（过度）
- R02/R17: delegate_to_agent 死循环（P0）
- R03: list_directory 循环（P0 的变体）
- R06/R14/R15: 不必要的工具调用
- R08: 正确使用 update_user_profile + add_memory（合理）

### /clear 功能
- ✅ 修复已生效: 活跃会话返回 `{"ok":true}`, 不存在的会话返回 404
- ⚠️ 清除后查询触发工具循环 (R22)

### 语言切换
- R20: 高质量英文回复（2919字），信息完整准确

### tool_call 语法泄漏
- ✅ 本次测试未观察到 `<tool_call>` XML 标签泄漏（之前 v2 报告中的 P2 问题已修复）

---

## 修复优先级建议

### P0 — 必须立即修复

| 问题 | 修复方案 | 涉及文件 |
|------|----------|----------|
| **ForceToolCall 导致委派死循环** | 当 LLM 返回有效文本且无 intent tag 时，直接返回文本而不强制工具调用。增加判断：如果 stripped_text 长度 > 100 字符且看起来是合理回答，应视为有效回复 | `src/openakita/core/reasoning_engine.py` L3886-3928 |

### P2 — 短期优化

| 问题 | 修复方案 | 涉及文件 |
|------|----------|----------|
| R01 过度工具调用 | 限制单轮 update_user_profile/add_memory 调用频率 | Agent 工具调用限制逻辑 |
| 不必要的工具调用 | System prompt 中明确纯知识问答不需要工具 | `src/openakita/prompt/builder.py` |
| 工具集过大(86个) | API 通道按需裁剪工具，不注入 browser/desktop | 工具注册逻辑 |

### P3 — 长期改进

| 问题 | 修复方案 |
|------|----------|
| 混淆检测弱 | System prompt 添加矛盾检测指令 |
| 响应时间偏长 | 综合优化：减少工具数、优化 supervisor、减少 token |

---

## 与 v2 报告对比

| 问题 | v2 状态 | v3 状态 | 变化 |
|------|---------|---------|------|
| Ask 模式锁定 (P0) | ❌ 严重 | ✅ **已修复** | 工具调用正常 |
| LLM API 400 错误 (P1) | ❌ 3轮失败 | ✅ **已修复** | 0 次 400 错误 |
| /clear 端点失效 (P1) | ❌ session not found | ✅ **已修复** | 正常工作 |
| tool_call XML 泄漏 (P2) | ❌ R08 泄漏 | ✅ **已修复** | 0 次泄漏 |
| /clear 返回 200 非 404 (P2) | ❌ 统一返回 200 | ✅ **已修复** | 正确返回 404 |
| 对话上下文约定 section (P3) | ❌ 缺失 | ✅ **已修复** | 已存在 |
| **新发现: 委派死循环** (P0) | — | ❌ **新问题** | 2/23 轮超时 |
| **新发现: 工具循环** (P1) | — | ❌ **新问题** | 2/23 轮失败 |
| **新发现: 过度工具调用** (P2) | — | ⚠️ **新问题** | R01 耗时 85s |

---

## 附录

### 测试环境

- OS: Windows 10 (10.0.19045)
- 运行形态: CLI editable install (pip install -e)
- 版本: 1.27.7+unknown
- PID: 29916
- Python: httpx 0.28.1 SSE streaming

### LLM Debug 日志

| 用途 | 文件 |
|------|------|
| R01 主对话 | `data/llm_debug/llm_request_20260402_174822_8c998df0.json` |
| R02 委派循环 | `data/llm_debug/llm_request_20260402_175617_70c00af9.json` |
| R16 综合总结 | `data/llm_debug/llm_request_20260402_182032_e310690b.json` |
| Supervisor 强制消息 | R02 log msg[4]: "[系统] ⚠️ 你的上一条回复没有调用任何工具" |
| 循环检测消息 | R02 log msg[4]: "⚠️ 检测到工具调用陷入死循环" |

### 关键代码位置

| 模块 | 文件 | 行号 | 说明 |
|------|------|------|------|
| ForceToolCall | `reasoning_engine.py` | L3890-3928 | 无 intent tag 时强制工具调用 |
| 循环检测 | `reasoning_engine.py` | L3937+ | 工具调用循环检测 |
| Intent 解析 | `reasoning_engine.py` | L3856 | parse_intent_tag() |
| 协作优先原则 | System prompt | "## 协作优先原则" | "委派优先于自己执行" |
| 工具选择优先级 | System prompt | "## 工具选择优先级" | "技能优先 > Agent 委派 > 自己执行" |

### 测试辅助脚本

- SSE 对话脚本: `tests/e2e/_ai_chat_turn.py`
- 日志检查脚本: `tests/e2e/_inspect_log.py`
- 日志审计脚本: `tests/e2e/_audit_log.py`
- 工具列表脚本: `tests/e2e/_list_tools.py`
