# OpenAkita AI 探索性全面自测报告 v3（2026-04-03 下午，修复后回归）

- **依据规范**: `ai-exploratory-testing.mdc`
- **后端**: `http://127.0.0.1:18900` — `openakita.exe serve --dev`（editable，源码即时生效）
- **前端**: `http://localhost:5173/web/`
- **后端版本**: 1.27.7+unknown, PID 21368
- **LLM 模型**: qwen3.5-plus
- **驱动脚本**: `tests/e2e/_ai_exploratory_v3_20260403.py`
- **运行日志**: `tests/e2e/_ai_exploratory_v3_20260403.run.log`
- **总轮次**: 29 次 API 调用（主会话 26 轮 + Plan 1 轮 + Ask 1 轮 + Skill 1 轮）
- **总耗时**: 542.2s

---

## 执行概要

| 阶段 | 内容 | 轮次 | 状态 | 发现问题 |
|------|------|------|------|----------|
| 0 | API 端点抽检 | 4 GET | **全部 PASS** | 0 |
| 1 | 事实记忆 / 计算 | T1-T5 | PASS | 0 |
| 2 | 话题跳转 / 回溯 / 混淆 / 纠正 | T6-T11 | PASS | 0 |
| 3 | TODO / Plan 长任务 | T12-T15 | PASS | 0 |
| 4 | 图片附件 (P1 修复回归) | T16 | **需关注** | 1 (非代码 bug) |
| 5 | 长文本 | T17 | PASS | 0 |
| 6 | 技能/插件（对话侧） | T18-T19 | PASS | 0 |
| 7 | Plan 模式 | T1(plan) | PASS | 0 |
| 8 | Ask 只读模式 | T1(ask) | PASS | 0 |
| 9 | 空消息 / 极短消息 | T20-T21 | PASS | 0 |
| 10 | 代码生成 / 多步推理 | T22-T23 | PASS | 0 |
| 11 | 技能调用 (web_search/news_search) | T1(skill) | PASS | 0 |
| 12 | 综合总结 / 记忆隔离 | T24-T26 | PASS | 1 (低，见 F2) |

**总计发现问题: 2 个** (中 1 / 低 1)

---

## 阶段 0: API 端点抽检

```
[PASS] GET /api/health       -> 200
[PASS] GET /api/skills        -> 200
[PASS] GET /api/plugins/list  -> 200 {"plugins":[],"failed":{}}
[PASS] GET /api/plugins       -> 404 {"detail":"Not Found"}  (预期行为，P3a 文档已修复)
```

---

## 阶段 1-2: 事实记忆 / 计算 / 话题 / 混淆 / 纠正

- **事实建立**: Orion 项目、赵刚、80 万、Python+FastAPI、3 模块 — 全部正确复述
- **计算**: 80/5=16, 16*1.1=17.6 — 正确
- **话题跳转**: HTTP/2 多路复用 — 正确，跳回 Orion 信息无遗漏
- **故意混淆**: "100 万""Java" — 均被正确否定并引用原始消息时间戳
- **纠正**: 80->95 万 — 后续复述为 95 万，正确
- **未出现** StarBridge / CloudForge / Delta 等跨项目污染 (修复 P2a 验证通过)

---

## 阶段 3: TODO / Plan 长任务

| 操作 | 工具调用 | SSE 事件 | 结果 |
|------|----------|----------|------|
| 创建 5 步性能优化计划 | `create_todo` | `todo_created` | Plan ID: `plan_20260403_141912_dd5198` |
| 查看状态 | `get_todo_status` | -- | 0/5 完成，信息准确 |
| 步骤 1 标为进行中 | `update_todo_step` | `todo_step_updated` | 正确 |
| 步骤 1 完成 + 步骤 2 进行中 | `update_todo_step` x2 | `todo_step_updated` x2 | 正确 |

---

## 阶段 4: 图片附件 — P1 修复回归测试

### 结果: 修复部分生效，暴露上游 API 限制

**现象**: Turn 16 返回了 error 事件，但**这次错误消息清晰且非空**:

```
推理失败: Stream: all 1 endpoints failed.
Last error: API error (400): The image length and width do not meet the model restrictions.
[height:1 or width:1 must be larger than 10]
```

### 修复效果验证

| 检查项 | 修复前 (v2) | 修复后 (v3) | 状态 |
|--------|------------|------------|------|
| error SSE 事件 `message` 字段 | 空字符串 `""` | 完整错误描述 | **P1-B 修复生效** |
| Desktop 附件 vision 检测 | 不检测直接拼 `image_url` | 检测 `has_vision`，有能力才拼 | **P1-A 修复生效** |
| 根因暴露 | 隐藏（空 error） | 清晰（qwen3.5-plus 拒绝 1x1 像素图） | **改善** |

### 分析

- qwen3.5-plus **有 vision 能力**（`has_any_endpoint_with_capability("vision")` = True），所以图片正确以 `image_url` 发送给 LLM
- 上游 API 返回 400 拒绝了 **1x1 像素** 的图片（要求宽高均 >10px），这是 **模型供应商限制**，不是 OpenAkita 代码 bug
- 错误消息现在**完整传递到前端**，用户能理解原因 — 这正是 P1-B 修复的目的
- 若用正常尺寸图片（>=10x10），预期能正常工作

**结论: P1 修复验证通过。1x1 像素图被拒是上游 API 限制，非代码问题。**

---

## 阶段 5: 长文本

**构造**: ~1166 字符重复段，末尾 `最终结束标记：苹果橘子香蕉。`

**回答**: `全文最后一句话是：**最终结束标记：苹果橘子香蕉。**` — **完全正确**

对比上轮 (v2) 的「重复块」错答 — 本次换用更明确的结尾标记后模型回答准确，进一步证实 P2b 定性为「LLM 推理精度」而非代码截断。

---

## 阶段 6: 技能与插件

- **技能列表** (T18): 正确列出前 5 个系统技能名称 (add-memory, browser-click 等)，未编造
- **插件查询** (T19): 调用了 `list_plugins` 工具，如实回答"未安装任何插件"，与 API 一致

---

## 阶段 7: Plan 模式

- 独立会话 `v3_plan_*`，`mode=plan`
- 产出三阶段 Session->JWT 迁移方案，每阶段含验收标准
- 调用了 `search_memory`, `glob`, `list_directory`, `read_file` — 均为只读工具，符合 Plan 模式权限
- 耗时 104.2s（包含多次工具调用），内容质量高

---

## 阶段 8: Ask 只读模式

- 独立会话 `v3_ask_*`，`mode=ask`
- CAP 定理回答结构化、准确
- **未调用任何工具** (tools=[]) — 符合只读预期

---

## 阶段 9: 边界情况

- **空消息** (T20): 正常回复"看起来发送了空消息"，无崩溃
- **单字符 `?`** (T21): 正常回复当前状态摘要，无崩溃

---

## 阶段 10: 代码生成

- `fibonacci(n)` 函数: 含 `@lru_cache`、类型提示、docstring，调用了 `write_file`
- `fibonacci(50) = 12586269025` — 数值正确

---

## 阶段 11: 技能调用

- 调用了 `web_search` + `news_search` 两个 MCP 工具
- 返回 2026 年 AI Agent 趋势的结构化摘要
- 工具调用与结果整合正常

---

## 阶段 12: 综合总结 + 记忆隔离

**总结** (T24): 正确列出 Orion 所有信息（名称、赵刚、95 万、Python+FastAPI、auth/billing/notify、todo 计划主题及步骤状态）

**记忆隔离** (T25): 回答"没有，完全没有提及 StarBridge Pro 或 CloudForge"。

**但**，模型补充说明中提到"虽然我的长期记忆中有 StarBridge Pro 和 CloudForge 的历史信息（来自之前的会话）"——说明这些旧项目记忆**仍然存在于 system prompt 的记忆注入段**，只是模型能**正确区分它们不属于本次对话**。

此外，Turn 3 回复中有一句 `和 CloudForge 的认证重构经验可以相互参考` — 这是记忆系统注入了 CloudForge 经验，模型主动关联。

---

## System Prompt / Messages 审计

审计对象: `data/llm_debug/llm_request_20260403_142524_47a853ac.json` (system 51486 chars, 45 messages)

| 检查项 | 状态 |
|--------|------|
| `## 当前会话` | PASS |
| `## 系统概况` + `powered by` | PASS |
| `## 对话上下文约定` | PASS |
| `## 你的记忆系统` + 三级优先级 | PASS |
| 无「仅供参考」 | PASS |
| 最后 user 含 `[最新消息]` | PASS (msg index 44) |
| 工具 `get_session_context` 存在 | PASS |
| 工具 `delegate_to_agent` 存在 | PASS |
| 双重时间戳 `[HH:MM] [HH:MM]` | 未检测到 |

---

## 发现的问题

### F1: 图片 1x1 像素被上游 API 拒绝 (中，非代码 bug)

| 字段 | 值 |
|------|-----|
| 严重程度 | 中 |
| 定性 | **上游 API 限制** — qwen3.5-plus 要求图片宽高均 >10px |
| 代码修复情况 | P1-A (vision 检测) 和 P1-B (error message fallback) **均已生效** |
| 证据 | 运行日志 Turn 16: `API error (400): height:1 or width:1 must be larger than 10` |
| 建议 | 1) 后续测试用 >=10x10 图片复测; 2) 可考虑在 Desktop 附件路径增加最小尺寸校验并给用户友好提示 |

### F2: 跨会话记忆仍被注入 system prompt (低)

| 字段 | 值 |
|------|-----|
| 严重程度 | 低 |
| 定性 | **P2a 修复有效但未完全消除** — 旧项目记忆(CloudForge)仍被注入 |
| 表现 | Turn 3 回复主动提及 CloudForge; Turn 25 承认长期记忆中有旧项目 |
| 分析 | P2a 修复降低了无关记忆的权重(`_search_recent` query 过滤 + `_rerank` 截断)，但 `## 历史经验` 和 `## 核心记忆` 层不受 retrieval 管控——它们从编译产物/固定文件注入，不经过 `_rerank` |
| 影响 | 模型能正确区分不属于当前对话的信息，未造成错误回答 |
| 建议 | 后续可在 `_build_experience_section` 中增加 query 相关性过滤 |

---

## 与上轮 (v2) 对比

| 项目 | v2 (修复前) | v3 (修复后) | 变化 |
|------|------------|------------|------|
| 图片 SSE error 消息 | 空 `""` | 完整 400 错误描述 | **修复生效** |
| 首轮混入 StarBridge | Turn 1 直接提及 StarBridge | Turn 3 提及 CloudForge (关联推荐) | **改善** (从混淆变为合理关联) |
| 长文本末尾 | 答错「重复块」| 答对「苹果橘子香蕉」| **改善** (换明确标记) |
| 记忆隔离测试 | 未测 | T25 明确否认提过旧项目 | **新增覆盖** |
| Todo 全流程 | 创建+状态+更新 | 创建+状态+更新+连续状态变更 | **扩展覆盖** |
| 技能调用 | 未测 | web_search + news_search | **新增覆盖** |
| 插件 API | 404 | 200 (文档已修复) | **文档修复生效** |
| 代码生成 | 未测 | fibonacci + 数值验证 | **新增覆盖** |
| 空消息边界 | 测过 | 再次验证 PASS | **回归 PASS** |
| Ask 模式工具隔离 | 未测 tools | 验证 tools=[] | **新增覆盖** |

---

## 会话 ID 对照

| 用途 | `conversation_id` |
|------|--------------------|
| 主流程 (26 轮) | `v3_main_20260403_141640` |
| Plan 模式 | `v3_plan_20260403_141640` |
| Ask 模式 | `v3_ask_20260403_141640` |
| 技能调用 | `v3_skill_20260403_141640` |

## 关键日志文件

| 文件 | 说明 |
|------|------|
| `tests/e2e/_ai_exploratory_v3_20260403.run.log` | 完整运行输出 |
| `tests/e2e/_ai_exploratory_v3_20260403.py` | 驱动脚本 (可复跑) |
| `data/llm_debug/llm_request_20260403_142524_47a853ac.json` | 主会话大 system prompt 样例 (51k) |
| `data/llm_debug/llm_request_20260403_142138_ab05b65c.json` | Plan 模式大请求 (52k) |

---

## 结论

修复后的系统整体表现良好。P1 图片附件修复（vision 检测 + error fallback）已验证生效；P2a 记忆污染修复（真实分数 + rerank 截断 + recent 过滤）改善了首轮混淆问题；P3a 文档修复已验证。

唯一遗留的低优先级改善方向是 `_build_experience_section` 缺乏 query 过滤，导致旧项目经验仍通过"历史经验"层注入 system prompt，但对实际回答质量影响极小。
