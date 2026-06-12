# OpenAkita 自进化系统完全使用手册

## 1. 目的与背景

### 1.1 什么是自进化系统

OpenAkita 自进化系统是一套多层级的自主改进引擎，使 Agent 能够：
- 从失败中学习并自动补全缺失能力
- 基于量化指标持续优化行为策略和运行时参数
- 从历史成功任务中提取高效工具调用模式
- 通过多 Agent 协作实现系统级改进

### 1.2 核心理念

从"坏了才修"的被动维修，转变为"主动实验、持续改进、有指标有回滚"的自主研究。

### 1.3 架构

```
┌─────────────────────────────────────────────────────────┐
│ P0: 实时层 — 失败即时响应                                │
│ AutoEvolver → 能力分析 → 依赖安装 → 技能生成             │
├─────────────────────────────────────────────────────────┤
│ P1: 周期层 — Benchmark 驱动实验循环 (每周一/四 02:00)    │
│ Benchmark → 实验循环 → 假设→修改→验证→保留/回滚          │
│ PromptOptimizer → 自动改进行为指令                        │
├─────────────────────────────────────────────────────────┤
│ P3: 学习层 — 工具模式学习 (每周日 05:00)                  │
│ PatternLearner → 从历史成功任务提取高效模式 → 注入 prompt │
├─────────────────────────────────────────────────────────┤
│ P4: 研究层 — 多 Agent 协作 (每月 1/15 号 01:00)          │
│ ResearchOrg → Analyst→Engineer→Auditor 协作              │
│ 高风险变更 → 审批队列 → 人工确认                          │
└─────────────────────────────────────────────────────────┘
```

---

## 2. 各模块详解

### 2.1 AutoEvolver (P0) — 自动进化响应器

**触发时机**: Agent 任务因 `missing_tool` 或 `insufficient_docs` 失败时自动触发。

**工作流程**:
1. `NeedAnalyzer` — LLM 分析任务描述，识别缺失能力
2. `AutoInstaller` — 尝试 `pip install` / `npm install` 安装依赖
3. `SkillGenerator` — 无法安装的能力自动生成 `SKILL.md` 技能

**去重**: 5 分钟内同一能力只处理一次。

### 2.2 BenchmarkEngine (P1) — 基准评估引擎

**内置任务 (8 项)**:
| 任务 | 类别 | 描述 |
|------|------|------|
| tool-file-edit | tool_use | 创建并验证文件 |
| tool-shell-exec | tool_use | 执行命令并验证结果 |
| code-fibonacci | coding | 实现斐波那契函数 |
| code-bug-fix | coding | 修复除零 Bug |
| research-web | research | 搜索最新特性 |
| memory-store-recall | memory | 存储并回忆信息 |
| writing-summary | writing | 中文摘要写作 |
| code-refactor | coding | 列表推导式重构 |

**评估指标**: 成功率、平均 Token、平均耗时、综合效率分、分类得分

**结果验证**: 关键词匹配检查输出是否符合预期（引号内容 + 数字）

### 2.3 ExperimentLoop (P1) — 实验循环

**功能**: 假设 → 修改 → Benchmark → 保留/回滚

**可修改目标**:
- `identity/AGENT.md` — Agent 行为指令
- `identity/POLICIES.yaml` — 策略配置
- `env:参数名` — `.env` 文件中的运行时参数（详见 3.4）

**安全护栏**:
- 白名单校验
- 路径遍历检测
- 变更比例 ≤30%
- 语法验证（Python AST / YAML safe_load）
- 模板变量 `{{}}` 平衡检查
- 模糊匹配（difflib + 空白归一化）
- 成功率不下降硬约束
- 自动回滚

**改进判定公式**:
```
score = 0.5(sr_new - sr_old) + 0.3(tok_saved/tok_old) + 0.2(time_saved/time_old)
保留条件: score > threshold AND sr_new >= sr_old
```

### 2.4 PatternLearner (P3) — 工具模式学习器

**功能**: 从历史 ReAct trace 提取高效工具序列 → 总结为 best practices → 注入 prompt

**注入格式**:
```
## 高效工具使用模式（从历史经验学习）
- 在修改代码文件时，应该 grep 定位 → read_file 确认 → edit_file 修改 → read_lints 检查
```

**特性**: 增量学习 (last_learn.json)、Jaccard 语义去重、500 字符截断

### 2.5 ResearchOrg (P4) — 多 Agent 研究组织

**角色分工**:
| 角色 | 职责 |
|------|------|
| Analyst | 分析性能 + 失败 + 工具统计 → 识别改进机会 |
| Prompt Engineer | 生成 prompt 修改方案 |
| Tool Developer | 设计新工具/技能规范 |
| Safety Auditor | 审查安全性（核心逻辑/漏洞/性能退化） |

**审批**: 高风险变更 → 审批队列 → 前端人工确认 → 应用

### 2.6 动态 Benchmark 生成器 (DynamicBenchmarkGenerator)

**功能**: 对高成功率任务自动生成更难变体 → 防止进化停滞

**触发条件**: `动态Benchmark成功率 > 95%` → 生成变体 → 写入 `draft_tasks.json` → 前端审批 → 加入正式任务池

**任务池**: 8-30 个，自动淘汰

### 2.7 .env 参数动态调优 (EnvTuner)

**功能**: 实验循环可修改 `.env` 中的运行时参数

**可调参数**:
| 参数 | 默认 | 范围 | 需重启 |
|------|------|------|--------|
| BENCHMARK_MAX_CONCURRENT | 1 | 1-8 | 否 |
| EXPERIMENTS_PER_CYCLE | 3 | 1-5 | 否 |
| BENCHMARK_TASK_TIMEOUT | 600 | 120-3600 | 否 |
| EXPERIMENT_LLM_TIMEOUT | 600 | 60-1800 | 否 |
| QUALITY_WEIGHT_IN_IMPROVEMENT | 0.3 | 0-0.8 | 否 |
| PROMPT_MAX_CHANGE_RATIO | 0.2 | 0.05-0.5 | **是** |

---

## 3. 使用方法

### 3.1 前端监控面板

**路径**: 侧边栏 → 监控 → 自进化

| Tab | 功能 |
|-----|------|
| 概览 | 健康度/成功率/Token/耗时 + ECharts 趋势图 + 最近实验 |
| 实验 | 筛选(全部/采纳/回滚) + 描述/指标/状态 |
| 技能 | 自动生成技能卡片 + 启用/禁用 + 删除 |
| 模式 | 工具模式列表 + 置信度/证据数 + 注入开关 |
| Prompt | 变体历史 + 采纳/拒绝 + Diff 对比 |
| 审批 | 审批队列 + 待审核 Benchmark 任务 |

### 3.2 配置开关

`config.py` 中：
```python
auto_evolve_enabled = True          # 失败自动进化
benchmark_evolve_enabled = True     # Benchmark 实验循环
prompt_optimize_enabled = True      # Prompt 优化
pattern_learn_enabled = True        # 工具模式学习
research_org_enabled = True         # 研究组织
dynamic_benchmark_enabled = True    # 动态任务生成
env_tuning_enabled = True           # .env 参数调优
```

### 3.3 定时任务

| 任务 | CRON | 说明 |
|------|------|------|
| benchmark_evolve | 每周一/四 02:00 | Benchmark + 实验循环 |
| pattern_learn | 每周日 05:00 | 工具模式学习 |
| research_org | 每月 1/15 01:00 | 多 Agent 研究周期 |

**手动触发**: 前端 → 定时任务 → 点击任务 → 手动执行

### 3.4 审批流程

1. 实验循环或研究组织产生**高风险变更** → 进入审批队列
2. 前端审批 Tab → 查看变更详情（Diff）→ 批准/拒绝
3. 拒绝需填写原因
4. 动态 Benchmark 变体 → draft_tasks → 审批 → 批准后加入任务池

### 3.5 本地模型优化

```python
# config.py
benchmark_max_concurrent = 1      # 串行，不冲垮模型
benchmark_task_timeout = 600      # 600 秒超时
experiment_llm_timeout = 600      # 实验 LLM 超时
experiments_per_cycle = 1         # 每周期只做一轮实验
```

或通过 `.env`：
```
BENCHMARK_MAX_CONCURRENT=1
BENCHMARK_TASK_TIMEOUT=600
```

### 3.6 数据文件

```
data/evolution/
├── benchmarks/
│   ├── tasks.json              # 正式任务池
│   ├── draft_tasks.json        # 待审批任务
│   ├── baseline.json           # 基线指标
│   └── results/                # 历史结果
├── experiments/                # 实验记录
├── patterns/                   # 工具模式
├── prompt_variants/            # Prompt 变体
├── research/                   # 研究周期结果
├── approvals/                  # 审批队列
├── metrics/                    # 运行时指标快照
└── quality_scores/             # 对话质量评分
```

---

## 4. 使用流程

### 4.1 日常使用

```
1. 正常使用 Agent 进行对话/任务
2. 系统自动记录 trace 到 data/react_traces/
3. 定时任务按计划自动运行（无需人工干预）
4. 查看前端监控面板了解系统状态
```

### 4.2 审批流程

```
1. 定时任务运行完成后 → 检查审批 Tab
2. 查看待审批的高风险变更
3. 查看 Diff 详情
4. 批准/拒绝（填写原因）
```

### 4.3 Benchmark 任务管理

```
1. 系统自动生成变体 → draft_tasks.json
2. 前端审批 Tab → 审批
3. 批准后自动加入 tasks.json
4. 下次 benchmark 运行时生效
```

### 4.4 调优 .env 参数

```
1. 实验循环自动提出 env: 目标
2. 修改 → Benchmark 验证 → 保留/回滚
3. 采纳后立即生效（不需重启的参数）
4. 需要重启的参数 → 提示用户手动重启
```

---

## 5. 注意事项

### 5.1 性能

- benchmark_evolve 执行时间较长（4 次 × 8 任务 × LLM 耗时），约 30+ 分钟（本地模型）
- 可在 config.py 中调整 `benchmark_max_concurrent` 控制并发
- 实验循环最多 3 轮，每轮运行完整 benchmark 验证

### 5.2 安全性

- 所有文件修改有白名单保护
- `.env` 修改有参数白名单 + 范围校验
- 实验失败自动回滚
- 高风险变更需人工审批
- 核心代码不可自动修改

### 5.3 本地模型

- 必须使用 `Brain.think()` 接口（非 `chat_simple`）
- 超时设置建议 ≥600s
- 并发建议 1（串行）
- 响应格式需支持 JSON（fence 包裹也可处理）

### 5.4 数据隐私

- 所有进化数据存储在 `data/evolution/` 本地目录
- 不上传任何数据到云端
- 用户反馈仅用于本地优化

### 5.5 常见问题

**Q: Benchmark 任务一直显示"执行中"**
A: 正常行为，4 次 benchmark 验证需要较长时间。可减小 `experiments_per_cycle` 加快。

**Q: 审批队列为空**
A: 只有当 RiskOrg 或 ExperimentLoop 产生高风险变更时才有审批。正常使用可能很少触发。

**Q: 自进化页面显示 100% 但定时任务显示失败**
A: 前端数据来自 `baseline.json`（历史数据），不影响当前运行。任务失败可在日志中排查具体错误。

**Q: draft_tasks.json 有任务但未加入正式任务池**
A: 需要前端审批 Tab 手动批准。或设置 `auto_approve_dynamic_tasks = True`。

---

## 6. 故障排查

### 6.1 定时任务失败

1. 检查日志 `logs/openakita-serve.log`
2. 常见错误：
   - `'Response' object has no attribute 'strip'` → Brain.think 返回类型问题
   - `AttributeError: 'Brain' object has no attribute 'chat_simple'` → 方法名错误
   - `TokenBudget...exceeded` → `scheduler_background_token_budget` 太小
3. 查看前端自进化页面 → 概览 Tab 确认健康度

### 6.2 LLM 调用失败

1. 确认 LMStudio 运行在 `localhost:1234`
2. 检查 `llm_endpoints.json` 配置
3. 检查超时设置（建议 ≥600s）

### 6.3 验证命令

```bash
# 单元测试
pytest tests/unit/test_evolution_system.py

# 缺陷回归测试
pytest tests/unit/test_defect_regression.py

# LMStudio 全流程测试
python tests/functional/test_brain_final.py

# .env 调优测试
python tests/functional/test_env_tuner.py
```
