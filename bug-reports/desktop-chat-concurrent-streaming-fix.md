# Desktop Chat API 并发流式传输修复方案

## 问题概述

Desktop Chat API (`/api/chat`) 使用单个 Agent 实例处理所有对话。当前端同时发起多个会话的流式请求时，Agent 实例上的共享可变状态会互相覆盖，导致：

- 工具执行读取到错误的 `conversation_id`（Plan 跟错会话、Memory 记到错误会话）
- `cancel` / `skip` 在无 `session_id` fallback 时取消了错误的任务
- `ReasoningEngine` 的 `_last_react_trace` 被覆盖，usage 统计/思维链数据错乱
- `_context_manager.set_cancel_event()` 被覆盖，导致旧会话无法被正确取消

**对比**：IM 通道走 `AgentOrchestrator` → `AgentInstancePool` → 每个会话独立 Agent 实例，不存在此问题。

**当前临时方案**：前端通过 `isAnyConvStreaming` 串行化请求（任何会话在流时，新消息自动排队）。

## 根因分析

### 1. Agent 实例上的共享可变状态

`src/openakita/core/agent.py` 中，以下字段在每次 `chat_with_session_stream()` 入口处被直接覆盖：

```python
# agent.py:3849-3851
self._current_session_id = session_id
conversation_id = self._resolve_conversation_id(session, session_id)
self._current_conversation_id = conversation_id
```

这两个字段在 Agent 内部被 **28 处代码** 读取（全部通过 `getattr(self, "_current_conversation_id", None)`），包括：

| 位置 | 用途 | 并发影响 |
|------|------|----------|
| `agent.py:4350-4351` | Plan handler 查找活跃 Plan | 读取到错误会话的 Plan |
| `agent.py:4621-4622` | 构建 system prompt 时获取 plan section | system prompt 包含错误会话的 Plan |
| `agent.py:4700-4701` | `_build_effective_prompt()` 注入 Plan | 同上 |
| `agent.py:4733-4734` | 判断是否需要 Plan 模式 | 误判 |
| `agent.py:4848` | 端点冷却期关联 conversation_id | 冷却记录错误 |
| `agent.py:4964` | 调试/日志 | 日志混乱 |
| `agent.py:5160-5161` | 工具执行中检查 Plan 状态 | 读取错误会话 Plan |
| `agent.py:5438-5439` | system_config 工具 Plan 检查 | 读取错误会话 Plan |
| `agent.py:5624` | `skip_current_step` fallback session_id | 跳过错误任务 |
| `agent.py:5647` | `insert_user_message` fallback session_id | 消息注入错误任务 |
| `agent.py:5690-5691` | CLI 模式 Plan prompt | N/A（CLI 不并发）|
| `agent.py:5842` | scheduler 关联 session_id | 计划任务关联错误 |
| `agent.py:5871` | Plan 需求判断 | 判断错误 |
| `agent.py:3612` | `_finalize_session` 自动关闭 Plan | 关闭错误会话的 Plan |
| `agent.py:3650` | `_cleanup_session_state` 重置任务 | 重置错误任务 |

### 2. ReasoningEngine 上的共享可变状态

`src/openakita/core/reasoning_engine.py` 中：

```python
# reasoning_engine.py:1135-1136
self._last_exit_reason = "normal"
self._last_react_trace = []
```

每次 `reason_stream()` 开始时重置。如果 A 的 `reason_stream` 正在执行，B 的 `reason_stream` 会将这些清零。

```python
# reasoning_engine.py:1161
self._context_manager.set_cancel_event(state.cancel_event)
```

`_context_manager` 是共享的，`cancel_event` 被 B 覆盖后，A 的取消操作将无效。

其他受影响的字段：
- `self._last_working_messages`（line 1267, 1085, 1915, 1929）— token 统计用
- `self._last_exit_reason`（line 919, 1638）— Plan 自动关闭判断

### 3. `_cleanup_session_state()` 共享状态清理

```python
# agent.py:3633-3655
def _cleanup_session_state(self, im_tokens):
    self._current_task_definition = ""
    self._current_task_query = ""
    self._current_session = None
    self.agent_state.current_session = None
    self._current_task_monitor = None
    _sid = getattr(self, "_current_session_id", None)
    # ... reset task for _sid
```

A 的流结束时调用，但 `_current_session_id` 已被 B 覆盖，导致错误地清理 B 的状态。

---

## 修复方案

### 方案：Desktop Chat API 使用 AgentInstancePool

与 IM 通道对齐，Desktop Chat API 也使用 `AgentInstancePool` 为每个会话分配独立的 Agent 实例。

#### 优点
- IM 通道已验证此模式的正确性，风险最低
- 不需要修改 Agent / ReasoningEngine 内部逻辑
- 天然支持 multi-agent profile（不同会话用不同 Agent profile）

#### 改动范围

##### 1. `src/openakita/main.py` — 确保 `_orchestrator` 始终初始化

当前 `_orchestrator` 仅在 `settings.multi_agent_enabled` 时初始化。Desktop Chat 需要 `AgentInstancePool` 即使不启用多 Agent 模式：

```python
# main.py 的 _serve() 或 start_im_channels() 中
# 改动：不论 multi_agent_enabled，都初始化 orchestrator（或至少初始化 pool）
# 提供给 API server 使用

# 方案 A：复用 orchestrator（推荐）
if _orchestrator is None:
    _orchestrator = AgentOrchestrator()
    if _message_gateway:
        _orchestrator.set_gateway(_message_gateway)

# 方案 B：独立 pool（轻量，不引入 orchestrator 的路由/委派/超时逻辑）
from openakita.agents.factory import AgentFactory, AgentInstancePool
_desktop_pool = AgentInstancePool(AgentFactory())
await _desktop_pool.start()
```

##### 2. `src/openakita/api/server.py` — 注入 pool

```python
# create_app() 中增加
app.state.agent_pool = agent_pool  # AgentInstancePool 实例
```

或复用 `app.state.orchestrator`（orchestrator 内部已有 `_pool`）。

##### 3. `src/openakita/api/routes/chat.py` — 核心改动

```python
@router.post("/api/chat")
async def chat(request: Request, body: ChatRequest):
    pool = getattr(request.app.state, "agent_pool", None)
    session_manager = getattr(request.app.state, "session_manager", None)

    if pool is None:
        # fallback：使用全局 agent（向后兼容，但不支持并发）
        agent = getattr(request.app.state, "agent", None)
    else:
        # 从 pool 获取 per-session Agent
        conversation_id = body.conversation_id or f"api_{uuid.uuid4().hex[:12]}"
        profile = _resolve_profile(body.agent_profile_id)  # 获取 AgentProfile
        agent = await pool.get_or_create(conversation_id, profile)

    return StreamingResponse(
        _stream_chat(body, agent, session_manager, http_request=request),
        ...
    )
```

同理，`/api/chat/cancel`、`/api/chat/skip`、`/api/chat/insert` 也需要从 pool 获取对应会话的 Agent 实例：

```python
@router.post("/api/chat/cancel")
async def chat_cancel(request: Request, body: ChatControlRequest):
    pool = getattr(request.app.state, "agent_pool", None)
    conv_id = body.conversation_id

    if pool and conv_id:
        # 从 pool 获取该会话的 Agent
        agent = pool.get_existing(conv_id)  # 需要新增此方法
    else:
        agent = getattr(request.app.state, "agent", None)

    actual_agent = _resolve_agent(agent)
    if actual_agent is None:
        return {"status": "error", "message": "Agent not initialized"}
    actual_agent.cancel_current_task(reason, session_id=conv_id)
    ...
```

##### 4. `src/openakita/agents/factory.py` — AgentInstancePool 新增方法

```python
class AgentInstancePool:
    def get_existing(self, session_id: str) -> Agent | None:
        """获取已有实例（不创建新的），用于 cancel/skip/insert 等控制操作"""
        with self._lock:
            entry = self._pool.get(session_id)
            if entry:
                entry.touch()
                return entry.agent
        return None
```

##### 5. `src/openakita/api/routes/chat.py` — `_stream_chat` 中的 profile 解析

需要一个辅助函数将 `agent_profile_id` 解析为 `AgentProfile` 对象（用于 `pool.get_or_create`）：

```python
def _resolve_profile(agent_profile_id: str | None) -> AgentProfile:
    """解析 AgentProfile，不存在时 fallback 到 default"""
    from openakita.agents.presets import SYSTEM_PRESETS
    from openakita.agents.profile import AgentProfile, ProfileStore
    from openakita.config import settings

    pid = agent_profile_id or "default"

    # 先检查系统预设
    for p in SYSTEM_PRESETS:
        if p.id == pid:
            return p

    # 再检查用户自定义 profile
    try:
        store = ProfileStore(settings.data_dir / "agents")
        profile = store.get(pid)
        if profile:
            return profile
    except Exception:
        pass

    # fallback 到 default
    for p in SYSTEM_PRESETS:
        if p.id == "default":
            return p

    # 最终 fallback：构造一个最小 profile
    return AgentProfile(id="default", display_name="Default Agent")
```

#### 资源考虑

`AgentInstancePool` 已有空闲回收机制（默认 30 分钟），Agent 实例不会无限增长。Desktop 场景通常只有 2-5 个活跃对话，资源占用可控。

如果担心资源消耗，可以考虑缩短 Desktop 场景的空闲超时：

```python
_desktop_pool = AgentInstancePool(AgentFactory(), idle_timeout=600)  # 10 分钟
```

---

## 前端联动改动（修复完成后）

后端支持并发后，前端需要移除串行化保护：

### `apps/setup-center/src/views/ChatView.tsx`

1. **移除 `isAnyConvStreaming` 及相关串行化逻辑**：
   - 删除 `isAnyConvStreaming` 计算（line ~1656）
   - `sendMessage` 中移除 "任何会话在流时阻止新请求" 的 for 循环（line ~2202-2205）
   - 键盘处理中移除 `else if (isAnyConvStreaming)` 分支（line ~3393）
   - 发送按钮中移除 `isAnyConvStreaming` 分支（line ~4033）
   - placeholder 中移除 `|| isAnyConvStreaming`（line ~3907）
   - `handleAskAnswer` 中移除排队逻辑，恢复直接 `sendMessage(answer)`（line ~2937）

2. **自动出队简化**：恢复仅按 `convId` 出队，移除 "所有流结束后跨会话出队" 逻辑

3. **`sendMessage` 请求体中添加 `agent_profile_id`**（如尚未包含）：

```typescript
const body = {
    message: text,
    conversation_id: convId,
    agent_profile_id: selectedAgent || "default",  // 确保后端 pool 能匹配 profile
    ...
};
```

---

## 测试要点

1. **基本并发**：A 会话发消息（流式中），切换到 B 会话发消息，两个流同时运行，互不干扰
2. **Plan 隔离**：A 有活跃 Plan，B 的 system prompt 中不应包含 A 的 Plan
3. **Cancel 隔离**：取消 A 不影响 B 的执行
4. **Skip/Insert 隔离**：向 A 插入消息不会进入 B 的任务队列
5. **Memory 隔离**：A 和 B 的 memory 记录分别保存到各自的 conversation_id
6. **Pool 回收**：会话空闲后 Agent 实例被正确回收
7. **Profile 切换**：不同会话使用不同 Agent profile，各自独立工作
8. **ask_user 跨会话**：A 在等待用户回答时，B 可以正常对话

---

## 文件清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/openakita/main.py` | 修改 | 始终初始化 pool 或 orchestrator，传给 API server |
| `src/openakita/api/server.py` | 修改 | `create_app` 接收并存储 `agent_pool` |
| `src/openakita/api/routes/chat.py` | 修改 | `/api/chat`、`/api/chat/cancel`、`/api/chat/skip`、`/api/chat/insert` 从 pool 获取 Agent |
| `src/openakita/agents/factory.py` | 修改 | `AgentInstancePool` 新增 `get_existing()` 方法 |
| `apps/setup-center/src/views/ChatView.tsx` | 修改 | 移除前端串行化保护（后端修复后执行） |
