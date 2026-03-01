---
name: skill-creator
description: 创建和改进 OpenAkita 技能。当需要：(1) 为重复性任务创建新技能，(2) 改进现有技能，(3) 将临时脚本封装为可复用技能时使用。技能是 OpenAkita 自进化的核心机制。
---

# Skill Creator — OpenAkita 技能创建指南

## 技能是什么

技能是模块化的、自包含的能力包，通过 SKILL.md 定义，扩展 OpenAkita 的能力。每个技能包含：
- `SKILL.md`（必需）：YAML frontmatter（name + description）+ Markdown 指令
- `scripts/`（可选）：可执行脚本（Python/Bash）
- `references/`（可选）：参考文档
- `assets/`（可选）：模板、图片等资源文件

## 何时创建技能

1. 同类操作第二次出现（持久化规则）
2. 用户明确要求创建技能
3. 任务涉及复杂多步流程且可能复用
4. 现有临时脚本需要升级为可复用能力

## 创建流程

### 1. 确定技能范围

明确技能要解决什么问题、触发条件是什么、需要哪些资源。

### 2. 创建技能目录

在 `skills/` 下创建目录：

```
skills/{skill-name}/
├── SKILL.md              # 必需：技能定义
├── .openakita-i18n.json  # 推荐：中文名和中文描述
├── scripts/              # 可选：可执行脚本
├── references/           # 可选：参考文档
└── assets/               # 可选：模板等资源
```

### 3. 编写 SKILL.md

**Frontmatter（必需）**：
```yaml
---
name: skill-name
description: 清晰描述技能功能和触发条件。description 是触发机制，必须说明"做什么"和"何时用"。
---
```

### 3.5 创建中文翻译文件（推荐）

在技能目录下创建 `.openakita-i18n.json`，提供中文显示名和描述：
```json
{
  "zh": {
    "name": "技能中文名（2-6个字）",
    "description": "技能功能的中文描述，简洁通顺。"
  }
}
```
此文件让中文用户在界面上看到友好的中文名称和说明。

**正文**：使用 Markdown 编写使用指令。原则：
- OpenAkita 已经很聪明，只写它不知道的信息
- 简洁优先，示例优于冗长解释
- 正文控制在 500 行以内，超出部分拆到 references/

### 4. 编写脚本（如需要）

将确定性操作封装为 `scripts/` 下的脚本：
- 脚本必须实际运行测试，确保无 bug
- 使用 `run_skill_script` 执行

### 5. 加载技能

创建完成后，使用 `load_skill` 将技能加载到系统中，使其在技能清单中可见。

## 改进现有技能

1. 使用 `get_skill_info` 查看当前技能内容
2. 修改 SKILL.md 或脚本文件
3. 使用 `reload_skill` 重新加载

## 关键原则

- **上下文窗口是公共资源**：技能与系统提示词、对话历史共享上下文，每一行都要值得其 token 开销
- **渐进式披露**：frontmatter 始终可见（~100词），正文仅在技能触发后加载，脚本按需执行
- **不要创建多余文件**：不需要 README.md、CHANGELOG.md 等辅助文件
