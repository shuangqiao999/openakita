# OpenAkita 后端架构与功能全览

## 概述

OpenAkita 是一个多智能体 AI 助手，基于 Python 3.11+ 异步架构，整合了完整的大模型调用、记忆系统、知识库、多 Agent 调度、自进化优化、多通道 IM 接入等能力。

**核心设计哲学**：不是"聊天机器人"，而是一个能够自我学习、自我优化、主动完成任务、永不放弃的 AI 团队。

---

## 1. 核心 Agent 系统

### Agent 主类

`core/agent.py` (9840 行) — 系统的中央协调器，构造函数初始化所有子系统：

| 子系统 | 组件 | 用途 |
|--------|------|------|
| 身份系统 | `Identity` | 加载 SOUL.md、AGENT.md、USER.md、MEMORY.md |
| LLM 脑 | `Brain` | 大模型交互层，封装 API 调用 |
| 推理引擎 | `ReasoningEngine` | ReAct 推理循环（流式） |
| 意图分析 | `IntentAnalyzer` | LLM + 规则混合意图分类 |
| 上下文管理 | `ContextManager` | 对话上下文压缩和预算控制 |
| Prompt 构建 | `PromptAssembler` | 分层组装 system prompt |
| 工具执行 | `ToolExecutor` | 工具调度引擎（支持并行） |
| 持久执行 | `RalphLoop` | "永不放弃"的持久任务循环 |

### 对话处理流水线

```
用户消息
  → IntentAnalyzer (CHAT/QUERY/TASK/COMMAND 分类)
  → PromptAssembler (组装 system prompt + 记忆注入)
  → ReasoningEngine.reason_stream() (ReAct 推理)
  → ToolExecutor.execute_batch() (工具执行)
  → 返回结果 / 继续推理
```

**流式对话入口**：`Agent.chat_with_session_stream(message, ...)` → SSE 事件流（每 token + 工具调用 + 完成）

### Ralph Loop — 永不放弃引擎

`core/ralph.py` — 核心执行循环。设计哲学：**持久化进度到 MEMORY.md，自动重启，自适应策略**。

```
while 任务未完成:
    1. 从 MEMORY.md 加载进度
    2. 执行一次推理迭代
    3. 检查结果
    4. 如果失败 → 分析原因 → 调整策略
    5. 保存进度到 MEMORY.md
    6. 继续下一轮
```

---

## 2. 记忆系统

### 三层架构

```
┌─ Semantic Memory (SQLite) ──────────────────┐
│  权威数据源: data/memory/openakita.db        │
│  表: memories, episodes, conversation_turns  │
│  FTS5 全文本索引 (含结巴中文分词)           │
├─ Vector Search (LanceDB) ────────────────────┤
│  向量存储 + 相似度搜索                       │
│  混合搜索: 向量 + FTS (RRF 融合)            │
│  嵌入缓存 + 熔断保护                         │
├─ Retrieval Engine ───────────────────────────┤
│  多路召回 + 重排序                           │
│  权重: 相关度40% + 重要度30% + 频次20% + 时新10%│
└──────────────────────────────────────────────┘
```

### 记忆类型

| 类型 | 说明 |
|------|------|
| 事实记忆 | 用户偏好、事件、知识点 |
| 剧集记忆 | 完整对话片段（episodes） |
| 便签本 | 当前任务临时状态（scratchpad） |

### 检索流程

1. **查询分解** — LLM 从用户消息中提取关键词和实体
2. **语义搜索** — LanceDB 向量 + FTS 混合搜索
3. **剧集搜索** — 时间范围和实体过滤
4. **近期记忆** — 最近 3 天高重要性记忆
5. **重排序** — 加权打分（相关度/重要度/访问频次/新鲜度）
6. **去噪** — 过滤低质量/自动生成的内容

每次检索后自动更新 `access_count` 和 `last_accessed_at`，用于后续排序优化。

---

## 3. 知识库

`knowledge/manager.py` (2163 行) — 独立的文档知识库，不同于记忆系统。

| 特性 | 说明 |
|------|------|
| 存储 | SQLite + LanceDB 双轨 |
| 导入 | 支持 PDF、DOCX、MD、TXT 及 30+ 代码格式 |
| 分块 | 智能分块（512 token 块，有重叠） |
| 搜索 | 向量 + FTS 混合搜索（RRF 融合） |
| 缓存 | 语义边缘缓存（200 条） |
| 大小 | 单文件最大 20MB |

输出到 `data/knowledge/knowledge.db`，可通过 API `/api/knowledge/*` 管理。

---

## 4. 多 Agent 系统

### 核心组件

| 组件 | 文件 | 用途 |
|------|------|------|
| `AgentOrchestrator` | `agents/orchestrator.py` | 中央协调，消息路由，健康监控 |
| `AgentFactory` | `agents/factory.py` | 从 AgentProfile 创建 Agent 实例 |
| `AgentInstancePool` | `agents/factory.py` | 实例池管理（按 session+profile 隔离） |
| `AgentProfile` | `agents/profile.py` | Agent 蓝图（角色/技能/prompt/类型） |
| `ProfileStore` | `agents/profile.py` | JSON 持久化（`data/agents/profiles/`） |

### 预设 Agent

| 名称 | 用途 |
|------|------|
| `default` | 通用助手 |
| `office-doc` | 办公文档处理 |
| `code-assistant` | 代码编写和审查 |
| `browser-agent` | 浏览器自动化 |
| `data-analyst` | 数据分析 |

### 委托流程

```
Root Agent 调用 delegate_to_agent(tool)
  → AgentOrchestrator._call_agent()
  → AgentFactory.get_or_create(profile)
  → Sub-Agent 收到完整历史 + 委派任务
  → Sub-Agent 执行 → 结果返回 Root
  → 最大委托深度: 5 层
```

---

## 5. 工具系统

### 工具执行引擎

`core/tool_executor.py` — 支持并行执行（`tool_max_parallel`）、信号量互斥（browser/desktop/mcp）、中断检查。

### 工具分类

| 类别 | 代表工具 |
|------|----------|
| 文件操作 | `read_file`, `write_file`, `edit_file`, `list_directory`, `glob`, `grep` |
| Shell 执行 | `run_shell`, `run_powershell` |
| Web 搜索 | `web_search` (6 引擎并行 + DDG/Bing 兜底) |
| Web 抓取 | `web_fetch`, `batch_web_fetch` |
| 浏览器 | `browser_open`, `browser_navigate`, `browser_click` (Playwright) |
| 记忆 | `add_memory`, `search_memory`, `get_memory_stats` |
| 多 Agent | `delegate_to_agent`, `delegate_parallel` |
| 桌面 | `desktop_click`, `desktop_type` (pyautogui) |
| 通信 | `deliver_artifacts`, `send_sticker` |
| 规划 | `create_todo`, `update_todo_step`, `complete_todo` |
| MCP | `call_mcp_tool`, `list_mcp_servers` |

### 渐进式工具披露

| 级别 | 说明 |
|------|------|
| Level 1 | Prompt 中只显示工具名 + 简短描述 |
| Level 2 | 通过 `get_tool_info` 获取完整 schema |
| Level 3 | 实际执行工具 |

高频工具（`run_shell`, `read_file`, `write_file` 等）始终加载完整 schema。

### 工具调频

| 工具 | 每轮上限 | 超限行为 |
|------|----------|----------|
| `web_search` | 8 次 | 提示 Agent 基于已有结果回答 |
| `news_search` | 8 次 | 同上 |
| `web_fetch` | 5 次 | 同上 |

---

## 6. IM 通道

### 统一网关

`channels/gateway.py` — `MessageGateway` 统一收发所有 IM 通道消息。

### 支持的通道

| 通道 | 适配器 |
|------|--------|
| Telegram | `adapters/telegram.py` |
| 飞书/Lark | `adapters/feishu.py` |
| 钉钉 | `adapters/dingtalk.py` |
| 企业微信 (Bot) | `adapters/wework_bot.py` |
| 企业微信 (WebSocket) | `adapters/wework_ws.py` |
| 微信个人 | `adapters/wechat.py` |
| QQ 官方 | `adapters/qq_official.py` |
| OneBot | `adapters/onebot.py` (通用 Bot 协议) |

---

## 7. 调度器

`core/scheduler/` — cron-like 定时任务系统。

### 检查机制

每 2 秒检查一次任务到期，最大并发 5 个任务，支持时区（`Asia/Shanghai`）。

### 内置定时任务

| 任务 | 默认频率 | 用途 |
|------|----------|------|
| `system:benchmark_evolve` | 周一/周四 02:00 | Benchmark 实验循环 |
| `system:pattern_learn` | 周日 05:00 | 工具模式学习 |
| `system:research_org` | 每月 1/15 日 01:00 | 多 Agent 研究周期 |
| `system:daily_memory` | 每日 03:00 | 记忆整理和压缩 |
| `system:daily_selfcheck` | 每日 04:00 | 系统自检和修复 |
| `system:workspace_backup` | 每日 02:00 | 工作区备份 |
| `system:proactive_heartbeat` | 每 2 小时 | 主动心跳 |
| `system:memory_nudge` | 周期性 | 记忆回顾 |

### 触发器类型

| 类型 | 示例 |
|------|------|
| `ONCE` | 一次性执行 |
| `CRON` | `"30 8 * * *"` (每天 8:30) |
| `INTERVAL` | 每 N 分钟/小时 |

---

## 8. 自进化系统

### 设计理念

让 AI 自己优化自己：通过 Benchmark 驱动的实验循环 + 失败驱动的自动补全，系统在两条路径上持续自我改进。

### 核心模块

| 模块 | 用途 |
|------|------|
| `BenchmarkEngine` | 8 项标准化任务评估 Agent 能力 |
| `ExperimentLoop` | 假设生成 → 修改 → 验证 → 保留/回滚 |
| `AutoEvolver` | 任务失败时自动分析 + 安装依赖 + 生成技能 |
| `SkillGenerator` | 自动编写 SKILL.md 技能文件 |
| `PatternLearner` | 从历史对话中学习高效工具调用模式 |
| `ResearchOrg` | 多 Agent 研究周期（分析师→工程师→审计师）|
| `DynamicBenchmarkGenerator` | 从真实对话 trace 生成场景化 Benchmark 任务 |
| `ConversationQualityEvaluator` | 对话质量评分 |
| `RuntimeMetricsCollector` | 运行时性能指标采集 |

### 实验循环

```
Benchmark → 基线指标
  → LLM 提出假设 (修改参数/文件)
  → 应用修改 (模糊匹配 + 语法验证 + 备份)
  → 重跑 Benchmark → _is_improvement() 加权判定
  → 改善 → 保留
  → 未改善 → 自动回滚
  → 重复 (每周期 3 次实验)
```

### 质量管线

```
每次实验 → _compute_quality_score() → save_score()
  → load_weekly_average() → 计算 quality_delta
  → _is_improvement() 加权: 50% 成功率 + 30% token + 20% 耗时 + quality_weight × quality_delta
  → adjust_quality_weight() 自适应: 0.10 → 0.25
```

### 失败自动进化（7 种 gap）

| gap 类型 | 触发条件 | 自动响应 |
|----------|----------|----------|
| `missing_tool` | 缺少工具 | pip 安装 + SkillGenerator 生成技能 |
| `insufficient_docs` | 知识不足 | pip 安装 + SkillGenerator 生成技能 |
| `supervision_gap` | 循环未检测 | 标记建议：调整 supervisor |
| `poor_context_engineering` | 上下文丢失 | 标记建议：调整压缩策略 |
| `budget_misconfigured` | Token 用尽 | 标记建议：递增预算 |
| `weak_verification` | 计划缺陷 | SkillGenerator 生成验证工具 |
| `missing_guardrail` | 护栏缺失 | 标记建议：调整安全策略 |

每次进化记录到 `data/evolution/evolution_history.jsonl`，每种 gap 有 60 秒速率限制。

### 用户反馈集成

聊天界面提供 👍👎 按钮。用户点击后通过 `POST /api/chat/feedback` 写入 `feedback.json`。

`ConversationQualityEvaluator.adjust_quality_weight()` 读取用户反馈 + 质量评分，计算 correlation：

```
correlation > 0.6 → weight + 0.01 (用户满意 ↔ 质评分高 → 增加质量权重)
correlation < 0.3 → weight - 0.01 (用户满意 ↔ 质评分低 → 减少质量权重)
无用户反馈 → 回退到质量趋势自适应
```

---

## 9. 技能系统

`skills/loader.py` — 从多个目录发现并加载 SKILL.md 文件。

### 加载顺序

```
__builtin__ → workspace → .cursor/skills → .claude/skills → skills/ → global home
```

### SKILL.md 格式

```markdown
---
name: my-skill
description: 描述
handler: my_handler
---
具体指引内容
```

支持内置技能和外部（Hub）技能。`SkillGenerator` 可在任务失败时自动生成新技能。

---

## 10. API 层

`src/openakita/api/` — FastAPI 应用，含 36 个路由文件。

### 主要端点分类

| 类别 | 端点 | 用途 |
|------|------|------|
| 对话 | `POST /api/chat` | 流式对话（SSE） |
| 对话 | `POST /api/chat/feedback` | 用户 👍👎 反馈 |
| 智能体 | `/api/agents/*` | Agent 配置管理 |
| 记忆 | `/api/memory/*` | 记忆查看/搜索/管理 |
| 知识库 | `/api/knowledge/*` | 文件导入/查询 |
| 技能 | `/api/skills/*` | 技能安装/管理 |
| 配置 | `/api/config/*` | 系统配置读写 |
| 会话 | `/api/sessions/*` | 对话历史管理 |
| 进化 | `/api/evolution/*` | 自进化状态 |
| 调度器 | `/api/scheduler/*` | 定时任务管理 |
| 日志 | `/api/logs/*` | 日志查看 |
| 健康 | `/api/health` | 系统状态 |

---

## 11. 配置系统

`config.py` (1572 行) — Pydantic BaseSettings，从 `.env` / 环境变量读取。

### 关键配置分类

| 分类 | 关键参数 |
|------|----------|
| LLM | `default_model`, `max_tokens`, API keys |
| Agent | `max_iterations=100`, `agent_name` |
| 工具 | `tool_max_parallel=1`, `tool_result_max_chars=32000` |
| 自进化 | `benchmark_evolve_enabled=True` |
| 记忆 | `embedding_model`, `search_backend=lancedb` |
| 多 Agent | `delegate_max_parallel=5`, `agent_state_ttl=30` |
| 可调参数 | `EVOLVABLE_ENV_PARAMS` 白名单（实验循环可改） |

---

## 12. 数据文件总览

| 路径 | 用途 |
|------|------|
| `data/memory/openakita.db` | 记忆数据库 (SQLite) |
| `data/knowledge/knowledge.db` | 知识库 (SQLite) |
| `data/react_traces/**/trace_*.json` | 对话 trace 记录 |
| `data/evolution/experiments/*_cycle.json` | 实验循环记录 |
| `data/evolution/experiments/quality_weight.json` | 质量权重 |
| `data/evolution/experiments/quality_scores/*.json` | 质量评分 |
| `data/evolution/feedback.json` | 实验 + 用户反馈 |
| `data/evolution/benchmarks/tasks.json` | Benchmark 任务池 |
| `data/evolution/benchmarks/draft_tasks.json` | 待审批变体任务 |
| `data/evolution/benchmarks/baseline.json` | 性能基线 |
| `data/evolution/metrics/*_snapshot.json` | 运行时指标 |
| `data/evolution/patterns/effective_patterns.json` | 高效工具模式 |
| `data/evolution/research/*_research_cycle.json` | 研究周期结果 |
| `data/evolution/evolution_history.jsonl` | 全部进化动作 |
| `data/evolution/approvals/*.json` | 审批队列 |
| `data/failure_analysis/{date}/{task_id}.json` | 失败分析结果 |
| `data/agents/profiles/` | Agent 配置 |
| `data/scheduler/tasks.json` | 定时任务持久化 |

---

## 快速启动

```bash
# 安装
pip install -e ".[dev]"

# CLI 交互模式
openakita

# API 服务模式
openakita serve

# 性能剖析
python profile_akita.py --iterations 2 --output reports/
```
