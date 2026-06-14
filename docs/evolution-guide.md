# OpenAkita 自进化系统

## 背景

OpenAkita 是一个多智能体 AI 助手，其行为由提示词（`identity/AGENT.md`、`identity/POLICIES.yaml`）和运行时参数（`.env`）共同决定。传统的调优方式依赖人工反复试错——修改参数、观察效果、再调整——效率低且容易顾此失彼。

自进化系统的核心理念是：**让 AI 自己优化自己**。通过 Benchmark 驱动的实验循环，系统自动提出改进假设、验证效果、保留有效改善、回滚无效尝试，形成一个完整的闭环。

```
┌─────────────────────────────────────────────────┐
│                                                    │
│  Benchmark → 假设生成 → 参数修改 → 重测 → 对比    │
│       ↑                                        ↓    │
│       └────────── 保留/回滚 ←──────────────────┘    │
│                                                    │
└─────────────────────────────────────────────────┘
```

## 触发方式

### 定时触发（推荐）

系统内置定时任务，默认可每 2 次/周自动运行：

| 任务 | 默认频率 | 控制开关 |
|------|----------|----------|
| `system:benchmark_evolve` | 周一/周四 02:00 | `.env` 中 `BENCHMARK_EVOLVE_ENABLED=True` |

启动服务后自动生效，无需干预：

```bash
openakita serve
```

### 手动触发

在 CLI 交互模式下运行 Benchmark：

```bash
openakita
>>> /benchmark
```

或直接运行单次实验循环：

```python
from openakita.evolution.benchmark import BenchmarkEngine
from openakita.evolution.experiment_loop import ExperimentLoop

engine = BenchmarkEngine()
report = await engine.run_suite(agent)

loop = ExperimentLoop(agent)
results = await loop.run_cycle(benchmark_report=report)

kept = [r for r in results if r.action == "keep"]
print(f"实验 {len(results)} 次，保留 {len(kept)} 项改进")
```

## 核心机制

### 1. Benchmark 引擎

运行 8 项固定任务，覆盖 5 个类别：

| 任务 | 类别 | 验证方式 |
|------|------|----------|
| `tool-file-edit` | tool_use | 文件内容校验 |
| `tool-shell-exec` | tool_use | 命令输出校验 |
| `code-fibonacci` | coding | 代码正确性 |
| `code-bug-fix` | coding | 修复结果验证 |
| `code-refactor` | coding | 重构后功能不变 |
| `research-web` | research | 搜索结果关键字 |
| `memory-store-recall` | memory | 记忆存储+召回 |
| `writing-summary` | writing | 总结内容关键字 |

每项任务产出 success / tokens / time 指标，加权汇总为效率分。

### 2. 实验循环

每个周期运行 3 轮实验：

- **假设生成**：LLM 读取当前 `AGENT.md`、`POLICIES.yaml` 和 14 个可调参数，提出改进假设
- **应用修改**：对文件做模糊匹配替换（容忍空白/格式差异）或修改 `.env` 参数值
- **语法验证**：Python 文件做 `ast.parse`，YAML 文件做 `yaml.safe_load`
- **重跑 Benchmark**：用修改后的配置跑一次完整 Benchmark
- **对比判定**：基于成功率、token 消耗、时间消耗、质量评分四个维度综合判断
- **保留或回滚**：改善则保留，未改善则恢复原状

### 3. 可调参数

系统可通过修改 `.env` 中的以下参数进行自动调优：

| 参数 | 默认值 | 范围 | 说明 |
|------|--------|------|------|
| `BENCHMARK_MAX_CONCURRENT` | 1 | 1-8 | Benchmark 并发数 |
| `EXPERIMENTS_PER_CYCLE` | 3 | 1-5 | 每周期实验次数 |
| `EXPERIMENT_IMPROVEMENT_THRESHOLD` | 0.02 | 0-0.2 | 改善判定阈值 |
| `QUALITY_WEIGHT_IN_IMPROVEMENT` | 0.10 | 0-0.30 | 质量分在改善判定中的权重 |
| `DYNAMIC_BENCHMARK_MAX_TASKS` | 30 | 10-50 | 动态任务池上限 |
| `BENCHMARK_TASK_TIMEOUT` | 600 | 120-3600 | 单任务超时(秒) |
| `RETRIEVAL_TOP_K` | 5 | 1-20 | 记忆检索返回条数 |
| `MEMORY_SIMILARITY_THRESHOLD` | 0.7 | 0.5-0.95 | 记忆匹配相似度阈值 |

全部参数定义在 `config.py` 的 `EVOLVABLE_ENV_PARAMS` 中。

### 4. 质量管线

用于辅助改善判定的数据驱动系统：

- **质量评分**：每次实验后根据 Benchmark 结果自动生成 `QualityScore`（relevance/correctness/completeness/efficiency）
- **质量趋势**：读取最近 7 天的评分，计算趋势 → 自适应调整 `quality_weight`
- **用户反馈关联**：将实验的 keep/discard 结果写入 `feedback.json`，与质量评分做 correlation 分析
- **质量权重自适应**：初始 0.10，每次周期 +0.01（质量趋势 >0.55 时），上限 0.25

### 5. 工具调用限频

防止 Agent 在单轮对话中无休止循环：

| 工具 | 每轮上限 | 超限行为 |
|------|----------|----------|
| `web_search` | 8 次 | 返回"已达到上限，请基于已有结果回答" |
| `news_search` | 8 次 | 同上 |
| `web_fetch` | 5 次 | 同上 |

### 6. 动态任务池

- **难度升级**：成功率 ≥95% 的任务自动生成 harder 变体 → `draft_tasks.json`
- **场景化生成**：从真实用户对话 trace 中提取任务类型，LLM 聚类生成新 Benchmark 任务
- **审批门控**：新任务先进入 `draft_tasks.json`，需人工审批后才进入正式任务池

### 7. 运行时监控

每次 Benchmark 周期自动采集：

- 记忆使用率（`access_count > 0` 的记忆占比）
- 工具调用频率 + 失败率
- 对话成功率 + 平均 token
- 用户纠正次数 + 重复查询次数

输出到 `data/evolution/metrics/` 目录。

## 数据文件说明

| 路径 | 用途 | 写入者 |
|------|------|--------|
| `data/evolution/experiments/*_cycle.json` | 每周期实验记录 | ExperimentLoop |
| `data/evolution/experiments/quality_weight.json` | 当前质量权重 | ExperimentLoop |
| `data/evolution/experiments/quality_scores/*.json` | 每次实验的质量评分 | ExperimentLoop |
| `data/evolution/feedback.json` | 实验 keep/discard 反馈 | ExperimentLoop |
| `data/evolution/benchmarks/baseline.json` | 性能基线 | BenchmarkEngine |
| `data/evolution/benchmarks/tasks.json` | 当前任务池 | DynamicBenchmarkGenerator |
| `data/evolution/benchmarks/draft_tasks.json` | 待审批变体任务 | DynamicBenchmarkGenerator |
| `data/evolution/metrics/*_snapshot.json` | 运行时指标快照 | RuntimeMetricsCollector |
| `data/evolution/approvals/*.json` | 审批队列 | ApprovalQueue |

## 注意事项

### Agent 重启

部分参数修改需要重启 Agent 才能生效（如 `PROMPT_MAX_CHANGE_RATIO`）。这些参数在 `EVOLVABLE_ENV_PARAMS` 中标记为 `needs_restart=True`，LLM 提出的实验会被自动标记为"需重启生效"。

### 成功率天花板

当 Benchmark 成功率接近 100% 时（8 个任务全部通过），系统会自动切换策略：成功率只要不低于 85% 就允许实验通过，主要依据 token 和时间消耗来判定改善。这样即使在"已最优"状态下，系统仍能通过降低资源消耗来持续优化。

### 质量权重磨合期

质量权重从 0.10 起步，每周期 +0.01，需要约 15 个周期（~2 个月）才能达到上限 0.25。如果想加速磨合，可在 `.env` 中手动设置：

```env
QUALITY_WEIGHT_IN_IMPROVEMENT=0.15
```

### Benchmark 任务难度

默认 8 个固定任务对 9B+ 模型来说偏简单。系统运行一段时间后，会自动通过 `generate_from_traces`（需要 ≥5 条真实用户对话记录）和 `generate_harder_variant`（需要任务成功率 ≥95%）生成更难的场景化任务，逐步提升评估区分度。

### 安全性

- 所有实验修改都通过 **模糊匹配** 定位原文 → 语 **法验证** → **备份** → 应用 → **回滚**，任何一步失败都回退
- `.env` 参数修改范围受 `EVOLVABLE_ENV_PARAMS` 白名单 + min/max 约束
- 文件修改限制在 `MUTABLE_TARGETS` 白名单内（`identity/AGENT.md`、`identity/POLICIES.yaml`）
- 审批队列对高风险变更需人工确认
- 工具调用有每轮上限，防止 Agent 无休止循环

### 性能剖析

系统附带独立的性能剖析脚本，可随时运行以了解各环节耗时：

```bash
python profile_akita.py --iterations 3 --output reports/
```

输出三份报告：`performance_report.json`、`bottleneck_analysis.json`、`optimization_suggestions.txt`。

支持与历史基准对比：
```bash
python profile_akita.py --compare reports/baseline.json
```

## 带来的变化与好处

### 短期（1-2 周）

- **参数自动收敛**：`BENCHMARK_MAX_CONCURRENT`、`RETRIEVAL_TOP_K` 等参数逐步调整到最优值
- **质量意识增强**：改善判定不再只看成功率，也考虑 token 消耗和回答质量
- **工具调用优化**：搜索类工具不会无休止循环

### 中期（1-2 个月）

- **个性化 Benchmark**：任务池从固定 8 个扩展到包含贴合实际使用场景的 20+ 个任务
- **质量权重收敛**：`quality_weight` 逐步找到最佳的 % 占比（0.15-0.25），改善判定更精准
- **记忆调优**：系统自动发现记忆使用率偏低时，建议调整检索参数

### 长期（3 个月以上）

- **持续自优化**：无需人工干预，系统根据用户的实际使用模式持续调整
- **退化检测**：任何改进如果导致性能退化，会被自动回滚
- **可审计**：每周期产生完整的实验记录、质量评分、反馈数据，随时可追溯优化历史

## 快速启动清单

```bash
# 1. 确保定时任务启用
echo "BENCHMARK_EVOLVE_ENABLED=True" >> .env

# 2. 首次手动跑一次验证（可选）
openakita
>>> /benchmark

# 3. 启动服务（定时任务自动生效）
openakita serve

# 4. 观察效果（第一次运行后查看数据）
ls data/evolution/experiments/
cat data/evolution/experiments/quality_weight.json

# 5. 性能剖析（可选）
python profile_akita.py --iterations 2 --output reports/
```
