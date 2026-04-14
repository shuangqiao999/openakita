# OpenAkita Agent 自动测试 Runbook

> 来源: `tests/interaction-refactor-test-plan.md` + `ai-exploratory-testing.mdc`  
> 目标: 让 Agent 可独立执行的自动化测试流程（不依赖人工 UI 点点点）

---

## 1) 适用范围

本 Runbook 只包含 **Agent 可自动执行** 的测试项:

- 阶段 0: 环境就绪验证
- 阶段 1: 后端基础设施验证（API + 代码审查）
- 阶段 5: IM 通道代码侧验证
- 阶段 6: AI 探索性多轮对话测试
- 阶段 7: 日志审计与报告输出

不包含 CLI 交互按键测试和 Desktop UI 人工体验测试（见 `manual-test-checklist.md`）。

---

## 2) 执行矩阵（两种运行形态）

| 运行形态 | 必跑内容 | 轮次要求 |
|---|---|---|
| CLI 安装态 | 阶段 0/1/5/6/7 全量 | 阶段 6 跑 20+ 轮 |
| EXE 打包态 | 阶段 0 + 阶段 6 回归子集 + 阶段 7 | 阶段 6 跑 8~10 轮 |

---

## 3) 执行步骤（Agent）

### Step A. 环境探活（阶段 0）

1. `GET /api/health`
2. `POST /api/health/check` with `{"dry_run": true}`
3. `GET /api/sessions`
4. `GET /api/commands?scope=desktop`
5. `POST /api/chat/clear` with test `conversation_id`

通过标准:
- 健康状态为 ok，至少一个 LLM 端点 healthy
- sessions ready
- commands 列表字段完整
- `/clear` 对不存在会话不报 500

### Step B. 后端基础验证（阶段 1）

1. SSE 一次对话，检查 `iteration_start/text_delta/done` 事件链
2. 关键模块代码审查:
   - `events.py` / `streamEvents.ts` 事件枚举一致
   - `utils/errors.py` 分类完整且被 CLI/gateway 引用
   - policy/session/sse 的健壮性修复点存在
3. `POST /api/config/env` 抽查热更新/重启字段行为

### Step C. IM 代码验证（阶段 5）

1. 审查 `text_splitter.py` 分片序号逻辑
2. 审查 `gateway.py` Smart reaction 逻辑
3. 审查 StreamPresenter 生命周期设计
4. 审查群聊上下文缓冲和 markdown 转纯文格式

### Step D. AI 探索测试（阶段 6）

按 `ai-exploratory-testing.mdc` 执行动态多轮对话:

- 必须走 SSE streaming
- 不写死断言，不预设固定对话树
- 每轮根据真实回复决定下一轮输入
- 维度至少覆盖:
  - 事实记忆
  - 计算追问
  - 话题跳转
  - 信息纠正
  - 远距离回溯
  - 故意混淆
  - 综合总结
- 轮次要求:
  - CLI 安装态: 20+ 轮
  - EXE 打包态: 8~10 轮（回归子集）

### Step E. 日志审计与报告（阶段 7）

1. 审计 `data/llm_debug/llm_request_*.json`
2. 核查 system prompt、messages 结构、工具定义
3. 输出结构化报告:
   - 对话表现
   - System Prompt 审计
   - Messages 结构审计
   - 问题列表（P0/P1/P2）

---

## 4) 报告输出要求

每种运行形态单独一份报告，不混写:

- `tests/e2e/report-cli-agent.md`
- `tests/e2e/report-exe-agent.md`

并追加一个对比结论:

- 仅 CLI 复现的问题
- 仅 EXE 复现的问题
- 两者都复现的问题

---

## 5) 失败处理策略

- P0（阻断/崩溃/数据错乱）: 立即停止后续阶段并开修复单
- P1（核心功能错误）: 允许继续收集样本，但标记需在发版前修复
- P2（体验/文案/边缘问题）: 可合并排期

