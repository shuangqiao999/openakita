---
name: tool-routing
description: Decision guide for choosing the right tool when operating websites, browsers, and desktop software. Consult when the task involves web interaction, website automation, or desktop app control.
system: true
category: System
priority: high
---

# 工具选择路由指南

当任务涉及操作网站、浏览器或桌面软件时，使用此指南选择最可靠的工具路径。

## 网站 & 浏览器操作

```
需要操作网站？
│
├─ 只需读取内容（文章、文档、API）
│   └─ web_fetch（最快，无需浏览器）
│
├─ 只需搜索信息
│   │
│   ├─ 用户兴趣属于权威主题领域？
│   │   └─ YES → fetch_bookmarked（技术新闻/AI研究/学术论文/开源动态/科技商业/官方文档 等）
│   │       └─ 比 web_search 更快、来源更可靠
│   │
│   └─ 通用搜索 / 非特定领域
│       └─ web_search（六引擎并行搜索）
│
├─ 需要交互（点击、填表、登录）
│   │
│   ├─ 目标网站有 opencli adapter？
│   │   └─ YES → opencli_run（最可靠，复用 Chrome 登录态）
│   │
│   ├─ 需要复杂多步交互？
│   │   └─ browser_task（自动规划步骤）
│   │       └─ 失败？→ 手动组合 browser_navigate + browser_click + browser_type
│   │
│   └─ 需要单步精确操作？
│       └─ browser_navigate / browser_click / browser_type 等
│
└─ 需要截图验证？
    └─ browser_screenshot → view_image
```

## fetch_bookmarked 适用领域

| 用户意图关键词 | purpose 参数 | 示例书签 |
|----------|----------|----------|
| 最新技术新闻、GitHub趋势、Hacker News | `daily_tech_news` | GitHub Trending, Hacker News, 阮一峰 |
| AI最新进展、OpenAI、DeepSeek | `ai_research` | OpenAI Blog, DeepSeek Blog, Anthropic |
| 学术论文、arXiv、顶会 | `academic_papers` | arXiv CS, ACL Anthology, Papers With Code |
| 开源项目、Gitee、GitHub动态 | `development` | Gitee 趋势, GitHub Changelog, 开源中国 |
| 技术博客、美团/微软工程 | `technical_blog` | 美团技术, Microsoft Dev, AWS Architecture |
| 科技商业、36Kr、VentureBeat | `tech_news` | 36氪, TechCrunch, VentureBeat |
| 公开数据集、World Bank | `public_data` | World Bank, Common Crawl, Data.gov |
| 国际新闻、Reuters | `general_news` | Reuters, Guardian, AP News |
| Stack Overflow 技术问答 | `technical_qna` | Stack Overflow, 思否 SegmentFault |
| Python/C++ 官方文档 | `official_doc` | Python Docs, C++ Reference |

## 桌面软件操作

```
需要控制桌面软件？
│
├─ 有 cli-anything CLI？（cli_anything_discover 检查）
│   └─ YES → cli_anything_run（最可靠，调用真实后端）
│
├─ Windows 系统？
│   └─ desktop_* 工具（UIA/pyautogui GUI 自动化）
│
└─ 有命令行工具？
    └─ run_shell（直接执行）
```

## 可靠性排序

### 网站操作（从高到低）
1. **opencli_run** — 确定性命令 + JSON 输出 + 登录态
2. **web_fetch** — 简单 HTTP 获取（仅读取）
3. **browser_navigate + browser_click/type** — 手动精确控制
4. **browser_task** — AI 自主操作（可能不稳定）
5. **call_mcp_tool("chrome-devtools")** — 需要额外配置

### 桌面软件操作（从高到低）
1. **cli_anything_run** — CLI 调用真实后端
2. **run_shell** — 系统命令行工具
3. **desktop_* 工具** — GUI 自动化（仅 Windows，脆弱）

## 关键原则

- **browser_task 失败不要反复重试** — 失败 1 次就切换到手动 browser_click/type 组合
- **搜索类任务不要用 browser_task** — 直接用 browser_navigate 拼 URL 参数更可靠
- **有 opencli adapter 时总是优先使用** — 比让 LLM 猜测页面操作可靠得多
- **有 cli-anything CLI 时优先使用** — 比 GUI 自动化可靠 100 倍

## 知识库与记忆检索场景

- 用户询问上传文档中的具体内容 → `search_knowledge_base`
- 用户问"我之前说过/喜欢什么/上次提到" → `search_memory`
- 知识库返回空 → 换更宽泛的关键词重试一次，仍空则告知用户
- 需要了解知识库中有哪些文档 → `list_knowledge_base_documents`

### 示例
用户：武松是哪个作品里的人物？
推理：这是事实性知识，应优先从知识库检索。
动作：search_knowledge_base(query="武松")

