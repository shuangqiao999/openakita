# OpenAkita EXE 阶段测试报告（Agent 执行）

> 测试形态: EXE 打包安装态  
> 执行日期: 2026-04-01  
> 用户提供: PID `32940`，基址 `http://127.0.0.1:18900`

---

## 1) 环境与就绪检查

- `GET /api/health` 返回 200，关键字段:
  - `status: ok`
  - `agent_initialized: true`
  - `pid: 32940`
  - `version_full: 1.27.7+3d648e7`
- `GET /api/commands?scope=desktop` 返回命令列表正常（含 `/help` `/clear` `/model` `/thinking`）

---

## 2) EXE 自动测试（8~10轮回归子集）

执行脚本:

```powershell
$env:PYTHONUTF8='1'
py -3.11 tests/e2e/test_context_retention_live.py --base-url http://127.0.0.1:18900
```

结果摘要:

- 实际轮次: 13（覆盖记忆、计算、回溯、纠正）
- 通过: 8
- 失败: 4

主要失败现象:

1. 多轮中间出现 endpoint timeout / 400 结构错误  
   - 典型错误: `InternalError.Algo.InvalidParameter: The content field is a required field`
2. 回溯轮次（Turn 11）因上述错误中断，导致记忆验证失败

---

## 3) 终端可执行“人工项”补充验证

### 3.1 工具调用可视化相关（以 SSE 事件验证）

测试输入:
- `请使用工具执行 python --version，并告诉我结果`

观察结果:
- SSE 事件仅有 `heartbeat`, `text_delta`, `done`
- **没有 `tool_call_start/tool_call_end` 事件**
- 回复正文出现 `<tool_call>...</tool_call>` 文本泄漏（工具调用标签未被执行通路消费）

判定:
- 工具调用链路异常（至少在此场景下），属于高优先级问题。

### 3.2 `/clear` 端点联动验证

流程:
1. 同一 `conversation_id` 发送“请记住我的昵称是小李”
2. 调 `POST /api/chat/clear`
3. 再问“刚才我说我的昵称是什么？”

结果:
- `/clear` 返回: `200 {"ok":false,"error":"session not found"}`
- 后续回复仍记住“小李”

判定:
- 前后端会话清理链路存在不一致（clear 对当前对话未生效或命中错误会话）。

---

## 4) 问题分级（EXE 阶段）

### P1（建议优先修复）

1. LLM 请求链路间歇出现 `content field is required`（影响多轮稳定性）  
2. 工具调用事件缺失且 `<tool_call>` 文本泄漏  
3. `/api/chat/clear` 与实际会话清理结果不一致

### P2

1. 局部 timeout（需结合模型端 SLA 与重试策略继续观察）

---

## 5) 证据与日志路径

- 测试报告:
  - `tests/e2e/report-exe-agent.md`
  - `tests/e2e/exe-manual-findings-triage.md`（人工测试问题登记与原因初判）
- LLM Debug:
  - `data/llm_debug/llm_request_*.json`
  - `data/llm_debug/llm_response_*.json`
- 服务日志:
  - `logs/openakita.log`
  - `logs/error.log`
  - `logs/openakita-serve.log`
  - `logs/frontend.log`

建议定位顺序:

1. 先按测试时间段筛 `logs/error.log`  
2. 对齐同时间 `llm_request/llm_response` 看 messages 与 tool calls 结构  
3. 再回看 `openakita.log` 中对应 request_id 的上下文

