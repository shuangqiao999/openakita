# 多 Agent 调度修复验证报告

## 验证环境

- **分支**: `new`（当前）
- **提交哈希**: `a8d80f435e62fafc0416619433205364a2deef07`
- **提交摘要**: feat: 多Agent调度系统全面重构 — LLM全局限流、工作窃取、DAG任务依赖支持
- **Lint 状态**: 0 errors（ruff check 全部通过）

---

## 一、功能默认启用状态

| # | 修复项 | 默认状态 | 详情 |
|---|--------|----------|------|
| 1 | delegate_parallel 强制创建独立 ephemeral Agent | **✅ 始终启用** | `tools/handlers/agent.py:213-225`：无条件为每个子任务调用 `base_profile.derive(... ephemeral=True)`，无任何 `if` 分支可绕过。 |
| 2a | LLM 并发信号量（max_concurrent） | **✅ 默认启用** | `config.py:152`：`llm_max_concurrent=8`，brain.py 中 `GlobalLLMRateLimiter().acquire()` 在每次 LLM 调用前无条件运行。 |
| 2b | LLM RPM 令牌桶 | **⚠️ 默认关闭** | `config.py:148`：`llm_rate_limit_rpm=0`（0=不限制）。当 rpm=0 时，`acquire()` 跳过令牌桶逻辑，仅保留并发信号量。 |
| 3 | 自适应并发控制器 | **⚠️ 默认关闭** | `config.py:756`：`enable_adaptive_concurrency=False`。仅在此开关为 True 时，`orchestrator.py:1480` 才会初始化 `AdaptiveConcurrencyController`。 |
| 4 | 工作窃取 (Work-Stealing) | **❌ 未实现为可配置项** | `config.py` 中**不存在** `enable_work_stealing` 配置项。`task_queue.py:60` 构造参数 `enable_work_stealing=False`，`orchestrator.py:281` 硬编码 `False`。用户无法启用。 |
| 5 | DAG 任务依赖 | **✅ 始终启用** | `tools/handlers/agent.py:166-173`：`TaskGraph.from_tasks_list()` 和 `validate()` 无条件调用。当 `depends_on` 为空数组时（向后兼容），拓扑分层输出单层，行为与旧版等价。 |
| 6 | 状态 TTL 清理（30秒） | **✅ 始终启用** | `config.py:166`：`agent_state_ttl=30`。`orchestrator.py:1493` 中 `_bg_cleanup_loop` 在 `start()` 时无条件启动，循环内按 TTL 清理。 |
| 7 | 字典容量上限驱逐 | **✅ 始终启用** | `orchestrator.py:264-266`：`_MAX_MAILBOXES=500, _MAX_HEALTH_ENTRIES=500, _MAX_SUB_STATES=500`。在 `_bg_cleanup_loop` 中无条件执行驱逐逻辑（行 1597-1624）。 |
| 8 | TaskQueue 接入委派路径 | **❌ 未接入** | 见下文「问题2」。 |

### 默认值评估

| 配置项 | 当前默认 | 建议默认 | 理由 |
|--------|----------|----------|------|
| `llm_rate_limit_rpm` | 0（关闭） | 保持 0，但标注清晰文档 | 不同 API 限额差异大（Anthropic 2000 RPM vs DeepSeek 500 RPM）。强制默认值易造成误限。建议在前端设置中提供一键预设按钮（如"Anthropic/DeepSeek 推荐"）。 |
| `llm_max_concurrent` | 8 | **保持 8** | ✅ 合理。覆盖 3-5 个并行子 Agent + 1 个主 Agent 的常见场景。 |
| `enable_adaptive_concurrency` | False | **建议改为 True** | 在已有并发信号量保护的前提下，自适应降速是安全网——延迟上升时自动降并发，不会影响正常场景，只会防止极限情况（如 API 降级）。风险极低。 |

---

## 二、模块引用完整性检查

### 2.1 新增模块

| 模块 | 引用路径 | 状态 |
|------|----------|------|
| `core/llm_rate_limiter.py` | `core/brain.py:734,826` → `GlobalLLMRateLimiter()` 在 `messages_create_async` 和 `messages_create_stream` 中实例化并调用 `acquire()`/`release()`/`report_rate_limited()` | **✅ 完整** |
| | `core/adaptive_concurrency.py:151-153` → `GlobalLLMRateLimiter().adjust_concurrency()` 自适应调参联动 | **✅ 完整** |
| `core/adaptive_concurrency.py` | `agents/orchestrator.py:1480-1486` → 在 `start()` 中按 `enable_adaptive_concurrency` 条件初始化 | **✅ 完整** |
| | `agents/orchestrator.py:1519-1520` → 在 `shutdown()` 中 `stop()` | **✅ 完整** |
| | `core/brain.py:762-767,861-867` → `record_latency()` 和 `record_rate_limited()` 在 LLM 响应后调用 | **✅ 完整** |
| `agents/task_graph.py` | `tools/handlers/agent.py:166-173` → `TaskGraph.from_tasks_list()` / `validate()` / `topological_layers()` 在 `_delegate_parallel` 中无条件调用 | **✅ 完整** |
| | `tools/handlers/agent.py:189,210,275,279` → `get_node()` / `mark_complete()` 用于任务调度和结果回写 | **✅ 完整** |

### 2.2 修改模块

| 模块 | 变更 | 回归风险 |
|------|------|----------|
| `config.py` | 新增 7 个配置项 | **低** — 均使用 `Field(default=...)`，不影响现有配置 |
| `tools/handlers/agent.py` | `_delegate_parallel` 重写 | **低** — 向后兼容 `depends_on` 缺省格式（空数组时等价于无依赖） |
| `agents/task_queue.py` | 增加 DAG/steal/cleanup | **低** — 所有新参数有默认值，旧调用 `enqueue(session, agent, payload, priority)` 仍正常工作 |
| `core/brain.py` | 集成限流器 | **低** — `GlobalLLMRateLimiter` 在 rpm=0 时 `acquire()` 几乎立即返回（仅 semaphore 开销） |
| `agents/orchestrator.py` | 大量增改 | **中等** — 新增方法不影响已有调用路径，`start()`/`shutdown()` 扩展了生命周期 |

---

## 三、发现的问题

### 问题1 [严重] `orchestrator.create_ephemeral_agent()` — 死代码

**位置**: `agents/orchestrator.py:1647`

`create_ephemeral_agent()` 方法已定义但**全局无调用**。`_delegate_parallel` 中的 ephemeral 创建直接在 handler 内部完成（`agent.py:217`），未使用此方法。该方法设计为供外部（如 TaskQueue handler）使用，但因问题2导致无调用路径。

### 问题2 [严重] TaskQueue handler 已设置但从未收到任务

**位置**: `agents/orchestrator.py:1490`（设置 handler），`orchestrator.py:1531`（handler 实现），`tools/handlers/agent.py:102,246,399`（委派直接调用 `orchestrator.delegate()` 绕过 TaskQueue）

**问题链**:
1. `orchestrator.py:1490`: `self._task_queue.set_handler(self._handle_queued_task)` — handler 正确设置
2. `orchestrator.py:1491`: `await self._task_queue.start()` — worker 正确启动
3. **但是**: `orchestrator.delegate()` → `_dispatch()` 直接使用 `asyncio.create_task`，从不调用 `self._task_queue.enqueue()`
4. **同时**: `_handle_queued_task` 中的 session 获取逻辑存在缺陷——尝试通过 `self._gateway.agent_handler._current_session` 获取 session，但 `agent_handler` 是 `MessageGateway` 上的一个 callable/函数，不是 Agent 实例，因此 `_current_session` 属性不存在。即使有 enqueue 调用，handler 也会在运行时失败。

**影响**: TaskQueue 框架虽已完整实现（含 DAG、work-stealing、cleanup），但因未接入委派路径，其优先级调度、并发队列、依赖解析等能力完全无法发挥。

### 问题3 [中等] 工作窃取不可配置且默认关闭

**位置**: `agents/orchestrator.py:281`，`agents/task_queue.py:60`

`enable_work_stealing` 没有对应的 `config.py` 配置项。在 `TaskQueue.__init__` 中默认 `False`，在 `Orchestrator.__init__` 中也被硬编码为 `False`。用户无法通过配置文件或前端设置启用此功能。

由于问题2导致 TaskQueue 未接入委派路径，即使启用工作窃取也会因无任务入队而空转。但若修复问题2后再启此功能，可带来跨 session 负载均衡。

### 问题4 [低] `adaptive_concurrency` 和 `brain.py` 不存在显式的集成调用

**位置**: `core/adaptive_concurrency.py:151-153`

`brain.py` 中的 `from .adaptive_concurrency import AdaptiveConcurrencyController` 只在 try/except 中作为可选行为（`except Exception: pass`），如果类实例化失败会静默跳过。这不影响核心功能，但若用户期望自适应并发工作，模块导入失败不会抛出可见错误。

---

## 四、集成决策建议

### 结论：**需要额外修改后再集成**

### 理由

当前提交完成了约 **70%** 的目标：

**已正确实现且可直接受益的功能**（无需用户配置）:
- ✅ 每个并行子任务使用独立 ephemeral Agent（真正并行）
- ✅ LLM 并发信号量保护（max 8，防止 API 连接池耗尽）
- ✅ DAG 任务依赖解析
- ✅ 子 Agent 状态 TTL 清理（30s，下游前端受益）
- ✅ 字典容量上限驱逐（防止长期运行内存泄漏）

**存在问题的功能**:
- ❌ TaskQueue 框架完整但未接入委派路径（**核心问题**）
- ❌ 工作窃取不可配置（需补充 config 项 + 修复问题2）
- ⚠️ 自适应并发默认关闭（建议改为默认 True 以降低风险）
- ⚠️ `create_ephemeral_agent` 为死代码

### 风险评估

- **不高**。已正确实现的部分不会破坏现有功能。问题2（TaskQueue 未接入）不影响现有 delegate 路径的正常运行——委托仍通过 `orchestrator.delegate()` → `_dispatch()` 路径工作，只是未获得优先级调度的增强。

### 集成前的必要修改清单

| # | 任务 | 优先级 |
|---|------|--------|
| M1 | 将 `orchestrator.delegate()` 中的 `_dispatch()` 调用改为通过 `self._task_queue.enqueue()` 入队，由 TaskQueue worker 调度执行 | **P0** |
| M2 | 修复 `_handle_queued_task` 的 session 获取逻辑——不能依赖 `gateway.agent_handler._current_session`；应从 queued_task 的 payload 中传递 session reference 或 session_id 并重新查找 | **P0** |
| M3 | 在 `config.py` 中添加 `enable_work_stealing: bool = Field(default=False)` 配置项，并在 `Orchestrator.__init__` 中从配置读取 | **P1** |
| M4 | 将 `enable_adaptive_concurrency` 默认值改为 `True`，或在前端设置中提供明确的"推荐开启"引导 | **P1** |
| M5 | 移除 `create_ephemeral_agent` 死代码，或将其改为由 `_handle_queued_task` 调用，使 TaskQueue handler 能够创建独立 Agent 实例 | **P2** |

### 时间预估

- M1 + M2（TaskQueue 接入）: **2-3 小时**
- M3 + M4（配置补充）: **0.5 小时**
- M5: 可与 M1-M2 合并处理
- **总计**: ~3 小时即可将完成度从 70% 提升至 95%+
