# OpenAkita 推演引擎 (Deduction Engine)

基于 Kuzu 图数据库 + LanceDB 向量检索 + LLM 驱动的五阶段平行世界推演引擎。

---

## 一、功能概览

### 1.1 五阶段推演流水线

```
种子材料 (新闻/报告/小说)
    ↓
阶段 1: 本体生成 (Ontology) → LLM 自动定义实体/关系类型
    ↓
阶段 2: GraphRAG 构建 → 语义分块 + Jieba POS 实体提取 → LLM 三元组抽取 → Kuzu 图谱
    ↓
阶段 3: 智能体工厂 → Kuzu 图谱 Person 节点 → LanceDB 全文检索 → LLM 深度人格生成
    ↓
阶段 4: 并行模拟 → StrategicReasoner 候选策略生成 + trust_matrix 信任矩阵 + 双路 LanceDB 语义记忆
    ↓
阶段 5: 报告生成 → LLM 分析模拟结果 → 结构化推演报告 (风险预警 + 策略建议)
```

### 1.2 用户交互

| 功能 | 说明 |
|------|------|
| **文档上传** | 支持 txt/md/pdf/docx/py/js/ts/rs/go/java 等 20+ 格式，自动文本提取 |
| **推演前目标** | 用户输入愿景（如"希望AGI被严格监管"），注入所有智能体人格 |
| **实时干预** | 模拟运行中随时注入指令，以 priority=1.0 写入 LanceDB，立即影响下一轮决策 |
| **SSE 实时日志** | 推演过程中前端通过 EventSource 接收实时日志流 |

### 1.3 前端可视化

| 组件 | 技术 |
|------|------|
| 3D 知识图谱 | `react-force-graph-3d` |
| 推演控制面板 | 会话管理 + 启动/暂停/注入 |
| 实时指令框 | 模拟进行中显示 |

---

## 二、技术架构

### 2.1 模块结构

```
src/openakita/deduction/
├── models.py              # 数据模型 (Session/Ontology/Agent/Report 等)
├── engine.py              # 主引擎 (create/list/start/delete session)
├── orchestrator.py        # 五阶段流水线调度器
├── preprocessor.py        # 预处理 (语义分块 + 实体提取 + LanceDB 索引 + 混合检索)
├── ontology.py            # 阶段1: LLM 本体生成
├── graph_builder.py       # 阶段2: 实体驱动 GraphRAG 构建
├── agent_factory.py       # 阶段3: LanceDB 全文检索 → 深度人格生成
├── simulator.py           # 阶段4: 多 Agent 并行模拟 + 双路语义记忆
├── strategic_reasoner.py  # 阶段4: 候选策略生成 + 信任矩阵 + 启发式评分
├── reporter.py            # 阶段5: 结构化推演报告生成
├── store.py               # Kuzu 图存储适配器 (4 Node + 4 Rel 表)
└── session_store.py       # SQLite 会话 + 日志持久化
```

### 2.2 依赖基础设施

| 组件 | 版本 | 用途 |
|------|------|------|
| Kuzu | ≥0.11.0 | 嵌入式图数据库 (Cypher 查询) |
| LanceDB | 0.33.0 | 向量数据库 (cosine 检索 + 混合过滤) |
| Jieba | ≥0.42.1 | 中文分词 + POS 词性标注 + 实体提取 |
| NetworkX | ≥3.0 | 图算法 (中心性/社区发现) |
| TextChunker | 内置 | 语义段落分块 (中文标点优先级) |

### 2.3 数据隔离

```
data/lancedb/
├── deduction_chunks_{session_id}   # 静态原文语义块
└── deduction_events_{session_id}   # 动态模拟事件 (含 priority/event_type)

data/deduction/
├── sessions.db                     # 会话 + 日志
└── graphs/{session_id}/kuzu/       # Kuzu 图数据 (物理文件)
```

### 2.4 LanceDB 表设计

**静态块表 `deduction_chunks_{sid}`**:

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 块 ID |
| vector | float32[] | 768d 嵌入 (embeddinggemma-300m) |
| content | string | 完整块内容 (不截断) |
| session_id | string | 会话隔离 |

**动态事件表 `deduction_events_{sid}`**:

| 字段 | 类型 | 说明 |
|------|------|------|
| event_id | string | 事件 ID |
| vector | float32[] | 768d 嵌入 |
| content | string | 事件描述 |
| agent_id | string | 发起者 |
| round_number | int32 | 轮次 |
| priority | float32 | 优先级 (用户指令=1.0, 不可变目标=0.9, AI 事件=0.5) |
| event_type | string | 类型 (user_intervention/immutable_goal/user_inject) |
| session_id | string | 会话隔离 |

### 2.5 Kuzu 图模型

```
Node tables:
  Entity(id, name, type, description, properties)
  Chunk(id, content, source, chunk_index)
  Agent(id, name, persona, background, goals)
  Event(id, description, event_type, timestamp, agent_id)

Rel tables:
  RELATES(FROM Entity TO Entity, relation, weight, evidence)
  MENTIONS(FROM Chunk TO Entity, confidence)
  ACTED(FROM Agent TO Event, action, timestamp)
  PARTICIPATES(FROM Agent TO Entity, role)
```

### 2.6 双路语义记忆

```
_agent_decide(agent, round_number):
  Path A (静态) → preprocessor.retrieve_for_entity(agent.name)
    └── LanceDB deduction_chunks 表 → 原著背景片段
  Path B (动态) → preprocessor.retrieve_dynamic_events(agent.name)
    └── LanceDB deduction_events 表 → 模拟中生成的事件
  Path C (缓存) → self._event_history[-5:]
    └── 最近 5 条全局事件
```

### 2.7 StrategicReasoner 决策流程

```
reason(agent, world_state, round_number):
  1. 检索用户干预 → preprocessor.retrieve_latest_intervention()
  2. 加载不可变目标 → self._immutable_goals
  3. 构建信任概要 → self._trust_summary_for(agent_id)
  4. LLM 生成 N 个候选策略 → _CANDIDATE_PROMPT
  5. 启发式评分:
     - risk_level 惩罚 (high: -0.3, low: +0.1)
     - 干预指令加分 (关键词匹配: +0.5)
     - 目标对齐加分 (goals 匹配: +0.2)
     - 信任感知 (±0.2~0.3)
  6. 选择最高分策略
  7. 写入 trust_matrix
```

### 2.8 不可变目标注入 (Goal Persistence)

```
pre_goals → orchestrator._phase3_agents
  ├── agent_factory: {user_expectations} → 人格生成 Prompt
  └── LanceDB: add_event_memory(immutable_goal, priority=0.9, round=1)

simulator._agent_decide():
  └── reasoner: 每轮将 immutable_goals 注入 _CANDIDATE_PROMPT 顶层
       → 确保 20 轮后智能体依然"不忘初心"
```

---

## 三、API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/deduction/upload` | 上传种子文档 (txt/md/pdf/docx 等) |
| `POST` | `/api/deduction/session` | 创建推演会话 |
| `GET` | `/api/deduction/sessions` | 会话列表 |
| `GET` | `/api/deduction/session/{id}` | 会话详情 |
| `POST` | `/api/deduction/session/{id}/start` | 启动五阶段推演 |
| `POST` | `/api/deduction/session/{id}/inject` | 注入事件 (priority=1.0 写入 LanceDB) |
| `POST` | `/api/deduction/session/{id}/intervene` | 用户实时干预 |
| `POST` | `/api/deduction/session/{id}/pre-goal` | 设定推演前目标 |
| `GET` | `/api/deduction/session/{id}/graph` | 导出 Kuzu 图谱数据 |
| `GET` | `/api/deduction/session/{id}/report` | 获取推演报告 |
| `GET` | `/api/deduction/session/{id}/logs` | 获取日志 |
| `GET` | `/api/deduction/session/{id}/stream` | SSE 流式推演状态 |
| `DELETE` | `/api/deduction/session/{id}` | 删除会话 (自动清理 LanceDB + Kuzu) |

---

## 四、使用方法

### 4.1 基本推演

1. 打开桌面端，点击左侧「推演引擎」
2. 在「会话标题」输入名称
3. 粘贴种子材料到文本框，或点击「上传文档」选择文件
4. (可选) 在「推演目标」输入期望结局
5. 点击「创建推演会话」
6. 选中会话，点击「启动推演」
7. 观察 3D 知识图谱和实时日志

### 4.2 推演中干预

1. 推演运行中，右侧底部出现「干预指令」输入框
2. 输入指令（如"政府突然介入调查"）
3. 点击「注入」或按 Enter
4. 指令以 priority=1.0 写入 LanceDB
5. 下一轮起，所有智能体感知到此强制指令

### 4.3 查看结果

1. 推演完成后，点击会话可查看 3D 知识图谱
2. 调用 `GET /api/deduction/session/{id}/report` 获取结构化推演报告
3. 报告包含：摘要、关键事件、风险预警、策略建议

### 4.4 配置要求

**LLM 端点**: 在「LLM 配置」中配置至少一个 OpenAI-compatible 端点（或使用 LMStudio 本地模型）

**嵌入模型**: 在「LLM 配置 → 文本嵌入模型」中配置嵌入 API 地址和模型名（如 LMStudio 的 embeddinggemma-300m-qat）

**依赖安装**:
```bash
pip install kuzu>=0.11.0 networkx>=3.0
```

---

## 五、应用场景

### 5.1 企业战略推演

**场景**: 上传行业分析报告 → 设定"希望在反垄断调查中获胜"目标 → 推演多方博弈

**效果**: 智能体自动分化为 CEO/竞争对手/监管机构/分析师等角色，围绕政策合法性和市场份额展开多轮博弈，输出包含风险预警和策略建议的报告。

### 5.2 文学作品推演

**场景**: 上传《红楼梦》前 80 回 → 设定"希望探春最终掌家" → 多轮推演

**效果**: 智能体继承原著人格（贾宝玉的叛逆、王熙凤的精明、林黛玉的敏感），在模拟世界中产生符合人物弧光的自发性互动。

### 5.3 政策沙盘模拟

**场景**: 上传政策白皮书 → 注入突发事件（经济危机/自然灾害） → 观察各方反应

**效果**: 不可变目标确保智能体在长期推演中保持政策方向一致，实时干预可模拟黑天鹅事件的影响。

### 5.4 教育辩论

**场景**: 上传争议性议题材料 → 设定对立目标 → 观察 AI 辩论

**效果**: StrategicReasoner 为每个智能体生成 3 种候选策略并基于信任矩阵评分，产生有深度、有策略的对抗性对话。

### 5.5 剧本杀/TRPG DM

**场景**: 上传世界观设定 → 注入 NPC 阵营目标 → 推演剧情走向

**效果**: 用户作为"上帝棋手"实时干预（"突然出现密室"），AI NPC 自动适应局势变化。

---

## 六、配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `deduction_enabled` | true | 是否启用推演引擎 |
| `deduction_max_agents` | 200 | 最大智能体数量 |
| `deduction_default_rounds` | 10 | 默认模拟轮数 |
| `deduction_candidate_count` | 3 | 每轮候选策略数 (2-5) |
| `deduction_llm_temperature` | 0.3 | LLM 温度 |
| `deduction_graph_max_depth` | 3 | 图谱查询最大深度 |
| `deduction_data_dir` | data/deduction | 推演数据目录 |

---

## 七、测试

```bash
# 单元测试 (25/25)
pytest tests/unit/test_deduction_engine.py -v

# 全流程 LMStudio 测试 (47/47)
python tests/functional/test_deduction_full_pipeline.py

# 战略推理 + 干预测试 (17/17)
python tests/functional/test_reasoner_intervention.py

# 不可变目标持久化测试 (8/8)
python tests/functional/test_goal_persistence.py

# 代码加固验证 (13/13)
python tests/functional/test_p0_p3_hardening.py
```

---

## 八、已知限制

| 限制 | 说明 |
|------|------|
| LLMClient vs LMStudio | 本地 LMStudio 的 OpenAI-compatible API 响应格式需通过 `chat_fn` 绕过 |
| Kuzu 0.11.3 参数化 | 仅支持 MERGE `$param` 节点匹配，MATCH...SET 使用内联转义 |
| 古文 Jieba 词典 | `classic_names.txt` 为占位文件，需手工填入古文人物名 |
| 后端 PyInstaller 打包 | 需 CI 环境构建，kuzu 原生库约 4.7MB |
