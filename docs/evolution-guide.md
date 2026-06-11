# OpenAkita 自进化系统指南

## 概述

OpenAkita 自进化系统是一个多层级的自主改进引擎，使 Agent 能够从失败中学习、自动补全缺失能力、持续优化行为策略，并通过 Benchmark 量化验证改进效果。

**核心理念**：从"坏了才修"的被动维修，转变为"主动实验、持续改进、有指标有回滚"的自主研究。

---

## 系统架构

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

## 各模块详解

### P0: AutoEvolver — 自动进化响应器

**触发时机**: Agent 任务失败且根因为 `missing_tool` 或 `insufficient_docs` 时自动触发。

**工作流程**:
1. `NeedAnalyzer` 用 LLM 分析任务描述，识别缺失的能力
2. `AutoInstaller` 尝试 `pip install` 或 `npm install` 安装依赖
3. `SkillGenerator` 对无法安装的能力自动生成 `SKILL.md` 技能文件

**去重机制**: 5 分钟内同一能力的重复失败只处理一次，避免资源浪费。

**文件位置**: `src/openakita/evolution/auto_evolve.py`

**配置**:
- `auto_evolve_enabled: bool` (默认 `True`) — 是否启用失败自动进化

---

### P1: Benchmark 引擎 + 实验循环

#### BenchmarkEngine

**功能**: 定义标准化任务集，量化评估 Agent 性能。

**内置任务 (8 项)**:
| 任务 ID | 类别 | 描述 |
|----------|------|------|
| `tool-file-edit` | tool_use | 创建并验证文件 |
| `tool-shell-exec` | tool_use | 执行 shell 命令并验证结果 |
| `code-fibonacci` | coding | 实现斐波那契函数 |
| `code-bug-fix` | coding | 修复除零 bug |
| `research-web` | research | 搜索 Python 3.12 新特性 |
| `memory-store-recall` | memory | 存储并回忆信息 |
| `writing-summary` | writing | 中文摘要写作 |
| `code-refactor` | coding | 列表推导式重构 |

**评估指标**:
- `success_rate`: 任务成功率
- `avg_tokens`: 平均 Token 消耗
- `avg_time`: 平均耗时
- `efficiency_score`: 综合效率分
- `category_scores`: 各分类得分

**结果验证**: 使用关键词匹配检查 Agent 输出是否符合 `expected_outcome`。包含引号内容精确匹配 + 数字验证。

**文件位置**: `src/openakita/evolution/benchmark.py`

#### ExperimentLoop (实验循环)

**功能**: `假设生成 → 修改文件 → Benchmark 验证 → 保留/回滚`

**可修改的目标**:
- `identity/AGENT.md` — Agent 行为指令
- `identity/POLICIES.yaml` — 策略配置

**安全护栏**:
- 白名单校验：只允许修改 `MUTABLE_TARGETS` 中的文件
- 路径遍历检测：`is_relative_to()` 防止写到项目外
- 变更比例限制：替换区域 ≤ 30% 文件内容
- 替换内容最小长度：≥ 10 字符
- 语法验证：Python 文件 `ast.parse()`，YAML 文件 `yaml.safe_load()`
- 模糊匹配：`difflib.SequenceMatcher`（0.85 阈值）+ 空白归一化
- 成功率不下降：`success_rate` 降低时直接拒绝
- 并发互斥：`asyncio.Lock` 防止多实验同时修改同一文件
- 文件回滚：异常/取消时自动恢复原始内容

**改进判定公式**:
```
score_delta = 0.5 × (sr_new - sr_old) + 0.3 × (tok_saved/tok_old) + 0.2 × (time_saved/time_old)
保留条件: score_delta > improvement_threshold AND sr_new >= sr_old
```

**文件位置**: `src/openakita/evolution/experiment_loop.py`

---

### P2: PromptOptimizer — Prompt 自主优化器

**功能**: 分析性能数据 → 生成 Prompt 变体 → Benchmark 验证 → 采纳/回滚

**工作流程**:
1. 从最近 Benchmark 结果读取当前性能
2. LLM 提出 Prompt 改进方案（JSON 格式，含原文和替换内容）
3. 模糊匹配替换 + 语法验证
4. 运行 Benchmark 对比效果
5. `_is_improvement` 判定（成功率硬约束 + 加权公式）

**额外验证**:
- 模板变量平衡检查：`{{` 和 `}}` 数量必须相等
- 变更比例限制：≤ 20% 文件内容

**文件位置**: `src/openakita/evolution/prompt_optimizer.py`

---

### P3: PatternLearner — 工具模式学习器

**功能**: 从历史成功任务的 ReAct trace 中提取高效工具调用序列，总结为 best practices，注入到系统 prompt 中。

**工作流程**:
1. 扫描 `data/react_traces/` 中最近 7 天的成功任务 trace
2. 递归提取每个 trace 中使用的工具名称
3. 按类别聚类，筛选 tokens + 时间均低于中位数的"高效序列"
4. LLM 总结为一行 best practice
5. Jaccard 语义去重（相似度 ≥ 0.8 保留高置信度）
6. 注入到 `prompt/builder.py` 的 "高效工具使用模式" 段落

**注入格式**:
```
## 高效工具使用模式（从历史经验学习）
- 在修改代码文件时，应该 grep 定位 → read_file 确认 → edit_file 修改 → read_lints 检查
- 在搜索信息时，应该 web_search 获取链接 → web_fetch 读取详情
```

**输出限制**: 最多注入 500 字符，超过则截断。

**文件位置**: `src/openakita/evolution/pattern_learner.py`

**数据文件**: `data/evolution/patterns/effective_patterns.json`

---

### P4: ResearchOrg — 多 Agent 研究组织

**功能**: 多个专职 Agent 协作驱动系统进化。

**角色分工**:

| 角色 | 职责 | LLM 调用 |
|------|------|----------|
| **Analyst** | 分析性能数据 + 失败记录 + 工具统计，识别 3 个最大改进机会 | `_run_analyst` |
| **Prompt Engineer** | 针对 prompt 类机会生成具体修改方案（JSON） | `_engineer_prompt` |
| **Tool Developer** | 针对 tool 类机会设计新工具/技能规范（JSON） | `_engineer_tool` |
| **Safety Auditor** | 审查所有提案的安全性（是否破坏核心逻辑/引入漏洞/性能退化） | `_run_auditor` |

**审批流程**:
- 低/中风险：自动应用 + Benchmark 验证
- 高风险：提交到审批队列 → 前端审批→人工确认→应用

**文件位置**: `src/openakita/evolution/research_org.py`

---

## 定时任务调度

| 任务 | CRON | 说明 |
|------|------|------|
| `system:daily_selfcheck` | `0 4 * * *` | 每日自检：分析错误日志，自动修复工具问题 |
| `system:benchmark_evolve` | `0 2 * * 1,4` | 每周一/四：Benchmark 评测 + 实验循环 |
| `system:pattern_learn` | `0 5 * * 0` | 每周日：学习高效工具调用模式 |
| `system:research_org` | `0 1 1,15 * *` | 每月 1/15 号：多 Agent 研究周期 |

---

## 前端监控面板

**访问路径**: 侧边栏 → 监控 → 自进化

**页面 Tab**:

| Tab | 功能 |
|-----|------|
| 概览 | 健康度/成功率/Token/耗时指标卡 + ECharts 趋势图 + 最近实验摘要 |
| 实验 | 筛选(全部/采纳/回滚) + 描述/Δ指标/状态 |
| 技能 | 自动生成技能卡片 + 启用/禁用 + 删除 |
| 模式 | 工具模式列表 + 置信度/证据数 + 注入开关 |
| Prompt | 变体历史 + 采纳/拒绝 + Diff 对比视图 |
| 审批 | 待审批卡片 + 风险标签 + 批准/拒绝 + Diff + 拒绝原因 |

**API 端点** (14 个): `GET/POST/PUT/DELETE /api/evolution/*`

---

## 配置项

所有配置位于 `src/openakita/config.py`：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `auto_evolve_enabled` | `True` | 是否启用失败自动进化 |
| `benchmark_evolve_enabled` | `True` | 是否启用 Benchmark 实验循环 |
| `prompt_optimize_enabled` | `True` | 是否启用 Prompt 自主优化 |
| `pattern_learn_enabled` | `True` | 是否启用工具模式学习 |
| `research_org_enabled` | `True` | 是否启用多 Agent 研究组织 |
| `experiment_llm_timeout` | `600` | 实验 LLM 调用超时（秒） |
| `research_llm_timeout` | `600` | 研究周期 LLM 超时（秒） |
| `benchmark_max_concurrent` | `1` | Benchmark 并发任务数 |
| `benchmark_task_timeout` | `600` | Benchmark 单任务超时（秒） |
| `experiments_per_cycle` | `3` | 每实验周期最大实验数 |
| `experiment_improvement_threshold` | `0.02` | 实验改进阈值 |
| `prompt_improvement_threshold` | `0.05` | Prompt 采纳阈值 |
| `prompt_max_change_ratio` | `0.2` | Prompt 单次修改最大比例 |
| `research_max_proposals` | `2` | 研究周期最大提案数 |

---

## 数据文件结构

```
data/evolution/
├── benchmarks/
│   ├── tasks.json              # 自定义 Benchmark 任务
│   ├── baseline.json           # 当前基线指标
│   └── results/                # 历史 Benchmark 结果
├── experiments/
│   ├── *_cycle.json            # 实验周期的实验记录
│   └── backups/                # 文件备份（7天自动清理）
├── patterns/
│   ├── effective_patterns.json # 当前活跃工具模式
│   └── last_learn.json         # 增量学习进度
├── prompt_variants/
│   └── archive/                # Prompt 变体历史
├── research/
│   └── *_research_cycle.json   # 研究周期结果
└── approvals/
    └── *.json                  # 审批队列
```

---

## 快速使用指南

### 1. 查看进化状态

打开前端 → 监控 → 自进化 → 概览 Tab。查看健康度评分和 Benchmark 趋势。

### 2. 启用/禁用功能

```python
# config.py
auto_evolve_enabled = True    # 任务失败自动补全能力
benchmark_evolve_enabled = True  # Benchmark + 实验循环
prompt_optimize_enabled = True   # Prompt 优化
pattern_learn_enabled = True     # 工具模式学习
research_org_enabled = True      # 多 Agent 研究
```

### 3. 添加自定义 Benchmark 任务

编辑 `data/evolution/benchmarks/tasks.json`：

```json
[
  {
    "id": "my-custom-task",
    "description": "搜索最新的 Rust 异步运行时对比",
    "category": "research",
    "expected_outcome": "返回至少 2 个运行时名称和对比",
    "timeout_seconds": 600,
    "difficulty": "medium"
  }
]
```

### 4. 审批高风险变更

前端 → 监控 → 自进化 → 审批 Tab。查看待审批的高风险变更，点击"批准"应用或"拒绝"并填写原因。

### 5. 触发手动进化

```python
from openakita.evolution import AutoEvolver
evolver = AutoEvolver(agent)
result = await evolver.respond_to_failure(
    task_description="从网页提取表格数据",
    harness_gap="missing_tool",
    suggestion="建议使用 pandas read_html"
)
```

---

## 本地模型调优

若使用本地 LLM（如 LMStudio/Ollama），建议调整：

```python
# config.py
benchmark_max_concurrent = 1     # 串行执行，避免冲垮本地模型
benchmark_task_timeout = 600     # 600 秒超时
experiment_llm_timeout = 600     # 实验假设生成超时
research_llm_timeout = 600       # 研究周期 LLM 超时
experiments_per_cycle = 2        # 减少每周期实验数
research_max_proposals = 1       # 减少提案数
```

---

## 测试

```bash
# 单元测试
pytest tests/unit/test_evolution_system.py tests/unit/test_defect_regression.py

# 功能测试（需要 LMStudio 运行在 localhost:1234）
python tests/functional/test_evolution_live.py

# 全部测试
pytest tests/unit/test_evolution_system.py tests/unit/test_defect_regression.py tests/unit/test_memory_reconnect_and_scope.py
```
