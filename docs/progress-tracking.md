# 项目进度跟踪机制

## 1. 看板管理 (GitHub Projects)

### 1.1 看板列设置
```
Backlog → Todo → In Progress → Code Review → Testing → Done
```

### 1.2 卡片信息规范
每张任务卡片必须包含：
- **标题**: [模块] 简短描述 (例：[Data] 实现 UserRepository)
- **Assignee**: 负责人
- **Labels**: 
  - 优先级: `P0-Critical`, `P1-High`, `P2-Medium`, `P3-Low`
  - 类型: `feature`, `bug`, `refactor`, `test`
  - 模块: `data`, `domain`, `presentation`
- **Estimate**: 故事点 (1/2/3/5/8)
- **Due Date**: 截止日期
- **Checklist**: 完成标准

### 1.3 看板更新频率
- 开发者：任务状态变更时立即更新
- CTO：每日下班前检查看板一致性

## 2. 每日站会制度

### 2.1 时间安排
- **时间**: 每个工作日 10:00 (北京时间)
- **时长**: 15分钟 (严格计时)
- **形式**: 企业微信群语音会议

### 2.2 参与人员
- 必需：CTO、架构师、dev-a、dev-b
- 可选：DevOps (涉及部署问题时)

### 2.3 站会议程 (每人2分钟)
每位成员按顺序回答三个问题：

1. **昨天完成了什么？**
   - 具体完成的任务/功能
   - 提交的 PR 链接

2. **今天计划做什么？**
   - 今天要完成的任务
   - 预期交付物

3. **有什么阻塞问题？**
   - 技术难点
   - 依赖等待
   - 资源需求

### 2.4 站会记录模板
```markdown
## 站会记录 - 2026-04-08

### 参会人员
- [x] CTO
- [x] 架构师
- [x] dev-a
- [x] dev-b

### dev-a
- ✅ 昨天：完成 Room Database 配置，创建 User Entity
- 📋 今天：实现 UserDao 和 UserRepository
- ⚠️ 阻塞：无

### dev-b
- ✅ 昨天：完成 Jetpack Compose 环境配置
- 📋 今天：实现 ProfileScreen UI 组件
- ⚠️ 阻塞：等待 API 接口定义 (需架构师协助)

### 行动项
1. [架构师] 今天 14:00 前输出 API 接口定义文档 @architect
2. [CTO] 协调产品部确认 API 需求 @cto

### 会议时长
14分钟
```

## 3. 进度汇报机制

### 3.1 向 CEO 汇报频率
- **正式汇报**: 每2天一次 (逢偶数日 18:00)
- **紧急上报**: 发现严重阻塞问题时立即

### 3.2 汇报内容模板
```markdown
## 进度简报 - Day X (迭代第 Y 天)

### 整体进度
- 计划完成任务：X/Y (Z%)
- 实际完成任务：A/Y (B%)
- 进度偏差：±N%

### 已完成任务
1. [任务名称] - 负责人 - 状态：✅
2. [任务名称] - 负责人 - 状态：✅

### 进行中任务
1. [任务名称] - 负责人 - 进度：XX% - 预计完成：日期
2. [任务名称] - 负责人 - 进度：XX% - 预计完成：日期

### 阻塞问题
| 问题描述 | 影响范围 | 负责人 | 解决方案 | 预计解决时间 |
|---------|---------|--------|---------|-------------|
| ...     | ...     | ...    | ...     | ...         |

### 风险预警
- 🟢 正常 / 🟡 轻微延迟 / 🔴 严重风险
- 说明：...

### 明日计划
1. [任务名称] - 负责人
2. [任务名称] - 负责人

### 需要支持
- 需要 CEO 协调的事项
- 需要其他部门配合的事项
```

### 3.3 汇报渠道
- 正式汇报：使用 `org_submit_deliverable` 提交给 CEO
- 紧急沟通：使用 `org_send_message` (priority=1)

## 4. 里程碑检查点

| 检查点 | 时间 | 验收标准 | 负责人 |
|-------|------|---------|--------|
| 环境搭建完成 | Day 3 | CI/CD 流水线运行成功 | CTO |
| 核心模块开发完成 | Day 10 | 单元测试覆盖率 ≥70% | CTO |
| Alpha 版本交付 | Day 14 | 可运行演示版本 + 文档 | CTO→CEO |

## 5. 工具配置

### 5.1 GitHub Projects 设置
```yaml
Project: OpenAkita Android MVP
Columns:
  - Backlog
  - Todo
  - In Progress
  - Code Review
  - Testing
  - Done

Automations:
  - When PR opened → Move to "Code Review"
  - When PR merged → Move to "Testing"
  - When tests passed → Move to "Done"
```

### 5.2 企业微信群机器人
- 每日 9:55 自动提醒站会即将开始
- 每日 18:00 自动收集当日完成的任务
- PR 状态变更时自动通知审查人

---
*最后更新: 2026-04-07 | CTO 技术部*
