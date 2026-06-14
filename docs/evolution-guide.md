# OpenAkita 自进化系统

## 背景与目的

OpenAkita 是一个多智能体 AI 助手，其行为由提示词（`identity/AGENT.md`、`identity/POLICIES.yaml`）和运行时参数（`.env`）共同决定。传统调优依赖人工反复试错——修改参数、观察效果、再调整——效率低且难以量化效果。

自进化系统的核心理念是**让 AI 自己优化自己**。通过 Benchmark 驱动的实验循环 + 失败驱动的能力补全，系统在两条路径上持续自我改进：

```
路径 1: 主动优化 (Benchmark 驱动)
 Benchmark → 假设生成 → 参数/文件修改 → 重测 → 保留/回滚 → 循环

路径 2: 被动进化 (失败驱动)
 任务失败 → FailureAnalyzer 分析根因 → AutoEvolver 自动补全能力
```

## 触发方式

### 定时触发（推荐）

系统内置定时任务，默认每周运行 2 次：

| 任务 | 频率 | 控制开关 |
|------|------|----------|
| `system:benchmark_evolve` | 周一/周四 02:00 | `BENCHMARK_EVOLVE_ENABLED=True` |
| `system:pattern_learn` | 周日 05:00 | `PATTERN_LEARN_ENABLED=True` |
| `system:research_org` | 每月 1 日/15 日 01:00 | `RESEARCH_ORG_ENABLED=True` |

启动服务后自动生效：

```bash
openakita serve
```

### 手动触发

```bash
openakita
>>> /benchmark
```

### 失败自动触发

任务失败时（工具限制、预算耗尽、循环检测、上下文丢失、计划缺陷、护栏缺失），`ReasoningEngine` 自动调用 `AutoEvolver` 尝试补全缺失能力。无需任何配置，`auto_evolve_enabled=True` 即可（默认启用）。

---

## 核心机制

### 1. Benchmark 引擎

运行 8 项固定任务，覆盖 5 个类别：

| 任务 | 类别 | 验证方式 |
|------|------|----------|
| `tool-file-edit` | tool_use | 内容校验 |
| `tool-shell-exec` | tool_use | 输出校验 |
| `code-fibonacci` | coding | 正确性校验 |
| `code-bug-fix` | coding | 修复验证 |
| `code-refactor` | coding | 功能不变校验 |
| `research-web` | research | 关键字校验 |
| `memory-store-recall` | memory | 记忆存取验证 |
| `writing-summary` | writing | 关键字校验 |

每项任务产出 success / tokens / time，汇总为效率分。

### 2. 实验循环

每周期最多 3 轮实验：

- **假设生成** — LLM 读取当前 `AGENT.md` 和 `POLICIES.yaml` + 14 个可调参数，提出改进假设
- **应用修改** — 模糊匹配替换（容忍空白/格式差异）或 `.env` 参数修改
- **语法验证** — Python `ast.parse` / YAML `safe_load`
- **重跑 Benchmark** — 完整 8 任务校验
- **综合判定** — 基于成功率、token、时间、质量评分四维度加权计算
- **保留/回滚** — 改善则保留，未改善则恢复原状

### 3. 可调参数

| 参数 | 默认值 | 范围 | 说明 |
|------|--------|------|------|
| `BENCHMARK_MAX_CONCURRENT` | 1 | 1-8 | Benchmark 并发数 |
| `EXPERIMENTS_PER_CYCLE` | 3 | 1-5 | 每周期实验数 |
| `EXPERIMENT_IMPROVEMENT_THRESHOLD` | 0.02 | 0-0.2 | 改善阈值 |
| `QUALITY_WEIGHT_IN_IMPROVEMENT` | 0.10 | 0-0.30 | 质量分权重 |
| `DYNAMIC_BENCHMARK_MAX_TASKS` | 30 | 10-50 | 动态任务池上限 |
| `BENCHMARK_TASK_TIMEOUT` | 600 | 120-3600 | 单任务超时(秒) |
| `RETRIEVAL_TOP_K` | 5 | 1-20 | 记忆检索条数 |
| `MEMORY_SIMILARITY_THRESHOLD` | 0.7 | 0.5-0.95 | 记忆匹配阈值 |
| `MEMORY_RETRIEVAL_TUNING_ENABLED` | 1.0 | 0.0-1.0 | 是否开启记忆调优 |
| `MEMORY_USAGE_LOW_THRESHOLD` | 0.3 | 0.1-0.5 | 记忆使用率过低阈值 |
| `MEMORY_TUNING_COOLDOWN_HOURS` | 24 | 1-72 | 记忆调优冷却(小时) |

完整列表在 `config.py` 的 `EVOLVABLE_ENV_PARAMS` 中。

### 4. 质量管线

- **质量评分** — 每次实验后生成 `QualityScore`（relevance / correctness / completeness / efficiency），全局 Benchmark 水平 + 实验改善幅度双向加权
- **质量趋势自适应** — 读取 7 天内评分趋势，自动上下调整 `quality_weight`（+0.01/周期，上限 0.25）
- **feedback 关联** — 实验 keep/discard 写入 `feedback.json`，与质量评分做 correlation 分析
- **天花板松弛** — 成功率 ≥95% 时自动切换策略，基于 token/时间改善而非成功率

### 5. 失败自动进化（6 种 gap 全覆盖）

当任务失败时，系统自动分析根因并按 gap 类型执行对应策略：

| gap 类型 | 触发条件 | 自动响应 |
|----------|----------|----------|
| `missing_tool` | 工具能力不足 | NeedAnalyzer → pip/npm 安装 → SkillGenerator 自动编写技能 |
| `insufficient_docs` | 文档/知识不足 | 同上 |
| `supervision_gap` | 陷入循环未被检测 | 标记建议：降低 supervisor 灵敏度 + 添加循环检测规则 |
| `poor_context_engineering` | 上下文丢失 | 标记建议：调整压缩策略 + 扩大保留窗口 |
| `budget_misconfigured` | Token 预算耗尽 | 标记建议：递增预算 + 裁剪冗余描述 |
| `weak_verification` | 计划缺陷/验证不足 | 自动生成 verification 辅助技能 |
| `missing_guardrail` | 安全护栏缺失 | 标记建议：调整安全策略 + 加入审视列表 |

每种 gap 类型有 60 秒速率限制，防止级联失败导致进化雪崩。所有进化动作记录到 `data/evolution/evolution_history.jsonl`。

### 6. 工具调用限频

防止 Agent 在单轮对话中无休止循环：

| 工具 | 每轮上限 | 超限行为 |
|------|----------|----------|
| `web_search` | 8 次 | 返回 "已达到上限，请基于已有结果回答" |
| `news_search` | 8 次 | 同上 |
| `web_fetch` | 5 次 | 同上 |

### 7. 动态任务池

- **难度升级** — 成功率 ≥95% 的任务自动生成 harder 变体 → `draft_tasks.json`
- **场景化生成** — 从真实用户对话 trace 中提取任务类型，LLM 聚类生成新任务
- **审批门控** — 新任务先进入 `draft_tasks.json`，需审批后进入任务池

### 8. 审批队列

高风险变更（如修改 `AGENT.md`）需人工审批：

- 提交 → `pending` 状态
- 批准 → 自动应用 + 语法验证 → `applied`
- 连续 3 次应用失败 → `rejected`（自动）
- 拒绝 → `rejected`（人工）

### 9. 运行时监控

每次 Benchmark 周期自动采集：

- 记忆使用率（`access_count > 0` 的占比）
- 工具调用频率 + 失败率
- 对话成功率 + 平均 token
- 用户纠正次数 + 重复查询次数

输出到 `data/evolution/metrics/`。

---

## 数据文件

| 路径 | 用途 |
|------|------|
| `data/evolution/experiments/*_cycle.json` | 每周期实验记录 |
| `data/evolution/experiments/quality_weight.json` | 当前质量权重 |
| `data/evolution/experiments/quality_scores/*.json` | 每次实验的质量评分 |
| `data/evolution/feedback.json` | 实验 keep/discard 反馈 |
| `data/evolution/evolution_history.jsonl` | 全部进化动作历史 |
| `data/evolution/benchmarks/baseline.json` | 性能基线 |
| `data/evolution/benchmarks/tasks.json` | 当前任务池 |
| `data/evolution/benchmarks/draft_tasks.json` | 待审批变体任务 |
| `data/evolution/metrics/*_snapshot.json` | 运行时指标快照 |
| `data/evolution/approvals/*.json` | 审批队列 |
| `data/failure_analysis/{date}/{task_id}.json` | 失败分析结果 |

---

## 注意事项

### 成功率天花板

当 Benchmark 成功率 ≥95% 时，系统会自动切换策略：成功率只要不低于 85% 就允许实验通过，主要依据 token/时间消耗来判定改善。这确保"已经最优"的系统仍能持续降低资源消耗。

### 质量权重磨合

权重从 0.10 起步，每周期 +0.01，约需 15 个周期（2 个月）达到上限 0.25。如需加速：

```env
QUALITY_WEIGHT_IN_IMPROVEMENT=0.15
```

### 参数重启

部分参数修改需要重启 Agent 才能生效（`EVOLVABLE_ENV_PARAMS` 中 `needs_restart=True`）。LLM 提出的实验会自动标记"需重启生效"。

### Benchmark 任务难度

默认 8 个固定任务对 9B+ 模型偏简单。运行一段时间后，系统会自动通过 `generate_from_traces`（需 ≥5 条真实对话 trace）和 `generate_harder_variant`（需成功率 ≥95%）生成更难的场景化任务。

### 安全性

| 机制 | 说明 |
|------|------|
| 模糊匹配 + 语法验证 | 所有文件修改都经这两步校验 |
| 白名单约束 | 文件修改限于 `AGENT.md` / `POLICIES.yaml`，参数限于 `EVOLVABLE_ENV_PARAMS` |
| 备份 + 回滚 | 任何实验失败自动恢复原文件 |
| 审批门控 | 高风险变更需人工确认 |
| 速率限制 | 每种 gap 类型 60 秒内最多触发 1 次进化 |
| 工具限频 | 搜索类工具每轮有硬上限 |

### 性能剖析

随时运行以了解各环节耗时：

```bash
python profile_akita.py --iterations 2 --output reports/
```

与历史基准对比：

```bash
python profile_akita.py --compare reports/baseline.json
```

输出三份报告：`performance_report.json`、`bottleneck_analysis.json`、`optimization_suggestions.txt`。

---

## 变化与好处

### 短期（1-2 周）

- 参数自动收敛到最优值（`BENCHMARK_MAX_CONCURRENT`、`RETRIEVAL_TOP_K` 等）
- 改善判定不再只看成功率，综合考虑 token、时间、质量
- 搜索类工具不再无休止循环（token 消耗降低 80%+）

### 中期（1-2 个月）

- Benchmark 任务池从固定 8 个扩展到 20+ 个贴合实际的场景化任务
- 质量权重收敛到最佳占比（0.15-0.25），改善判定更精准
- 记忆使用率过低时自动建议调参
- 任务失败时 6 种 gap 类型自动响应

### 长期（3 个月+）

- 完全无需人工干预的持续自优化
- 退化自动检测和回滚
- 完整可审计的进化历史（每步都有记录）
- 系统行为随用户使用模式持续调整

---

## 快速启动

```bash
# 1. 启用定时任务（默认已启用）
echo "BENCHMARK_EVOLVE_ENABLED=True" >> .env
echo "AUTO_EVOLVE_ENABLED=True" >> .env

# 2. 启动服务
openakita serve

# 3. 首次手动验证（可选）
openakita
>>> /benchmark

# 4. 查看进化历史
cat data/evolution/evolution_history.jsonl

# 5. 性能剖析（可选）
python profile_akita.py --iterations 2 --output reports/
```
