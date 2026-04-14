# OpenAkita CLI 测试报告（Agent 执行）

> 测试形态: CLI 安装态  
> 执行日期: 2026-04-01  
> 执行范围: 自动测试 + 命令行人工可替代检查（终端）

---

## 1. 执行概览

- 后端启动方式: `openakita serve --dev`
- 健康检查: `GET /api/health` 返回 `200`，`agent_initialized=true`
- 命令注册表: `GET /api/commands?scope=desktop` 返回有效命令列表
- `/clear` 端点: 对不存在会话返回 `200` + `{"ok":false,"error":"session not found"}`（优雅处理）

---

## 2. 自动测试结果

### 2.1 上下文记忆实时测试

命令:

```powershell
$env:PYTHONUTF8='1'
py -3.11 tests/e2e/test_context_retention_live.py --base-url http://127.0.0.1:18900
```

结果:

- 通过 `10/12`
- 失败 `2` 项（均发生在“纠正记忆”后续轮次）

关键失败:

- 接口错误: `API error (400): InternalError.Algo.InvalidParameter: The content field is a required field`
- 影响: 信息纠正后验证步骤中断，导致“纠正后代号/人数”断言失败

### 2.2 综合 API E2E 测试

命令:

```powershell
$env:PYTHONUTF8='1'
py -3.11 tests/e2e/test_api_comprehensive.py
```

结果:

- Total: `25`
- Passed: `19`
- Failed: `6`
- With warnings: `7`
- 平均耗时: `51327ms`
- 结果文件: `tests/e2e/api_test_results.json`

失败类型分布:

- `TIMEOUT after 180s`: 多例
- LLM 400 结构错误: 多例（同 `content field is a required field`）

---

## 3. CLI 命令侧检查（终端）

已执行:

- `openakita --help` ✅
- `openakita init --help` ✅
- `openakita status` ✅（可执行）
- `openakita run "现在几点了"` ❌

`openakita run` 失败详情:

- 异常: `AttributeError: 'str' object has no attribute 'success'`
- 定位线索: `src/openakita/main.py` 的 `run()` 路径中对 `result.success` 的访问
- 结论: `run` 命令返回值类型与调用方期望不一致，属于 CLI 主路径缺陷

---

## 4. 风险分级（CLI 阶段）

### P1（发版前建议修复）

1. `openakita run` 崩溃（返回值类型不匹配）  
2. LLM 请求在部分场景触发 `content field is required`（导致多用例失败）

### P2（可并行排期）

1. 若干 case `TIMEOUT after 180s`（需区分模型稳定性 vs 上下文过大）
2. 预期工具调用未触发的告警（可能是模型策略行为，不一定是功能 bug）

---

## 5. 结论与建议

- CLI 形态基础能力可启动并运行，但存在明确稳定性问题（`run` 路径 + LLM 400）。
- 不建议直接进入 EXE 回归；建议先修复 P1 后再进行 EXE 验证。
- 若必须进入 EXE，可先做最小回归子集并重点观察同类错误是否复现。

---

## 6. 阶段收尾（按计划执行到 Step 5）

- 已执行 `openakita selfcheck`（结果整体 healthy，但存在 `API key missing` 自检提示项）。
- 已尝试发起第二轮回归脚本（`test_context_retention_live.py`、`test_api_comprehensive.py`），为进入 EXE 阶段前切换环境，进程在长时间运行后手动停止。
- 已完成 CLI 卸载：
  - `py -3.11 -m pip uninstall -y openakita` 成功
  - `openakita --help` 已不可用（符合卸载预期）
  - `py -3.11 -m pip show openakita` 显示未安装

---

## 7. 日志与证据文件路径（后续修复定位）

以下文件建议作为本轮问题定位输入一并保留：

- 测试总报告（人工整理）  
  - `tests/e2e/report-cli-agent.md`
- 综合 API 用例明细（结构化 JSON）  
  - `tests/e2e/api_test_results.json`
- LLM 请求/响应原始调试数据（定位 400/上下文构造问题）  
  - `data/llm_debug/llm_request_*.json`
  - `data/llm_debug/llm_response_*.json`
- 服务与运行日志（排查超时/异常堆栈）  
  - `logs/openakita.log`
  - `logs/error.log`
  - `logs/openakita-serve.log`
  - `logs/frontend.log`

建议排查顺序：

1. 先看 `tests/e2e/api_test_results.json` 找失败 case id  
2. 按时间戳对齐 `data/llm_debug/llm_request_*.json` 与 `llm_response_*.json`  
3. 再到 `logs/error.log` / `logs/openakita.log` 对照异常栈与请求时段

