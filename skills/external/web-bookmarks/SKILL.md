---
name: web-bookmarks
description: 从预设的高质量网址书签中定向抓取内容，替代通用搜索引擎以获得更快、更精准的结果。
---

# 网址书签定向抓取

本 skill 提供 `fetch_bookmarked` 工具的使用指导。该工具从预置书签中按用途分类批量抓取网址内容。

## 何时使用

当用户意图匹配以下任一场景时，优先使用 `fetch_bookmarked`：

| 用户意图 | 调用方式 |
|----------|----------|
| 最新技术新闻、GitHub 趋势、Hacker News | `fetch_bookmarked(purpose="daily_tech_news")` |
| AI 最新进展、OpenAI/Google/HuggingFace 发布 | `fetch_bookmarked(purpose="ai_research")` |
| 学术论文、arXiv、JMLR | `fetch_bookmarked(purpose="academic_papers")` |
| 开源项目动态、Gitee/开源中国 | `fetch_bookmarked(purpose="development")` |
| 技术团队博客、美团/Microsoft | `fetch_bookmarked(purpose="technical_blog")` |
| 科技商业新闻、36Kr/VentureBeat | `fetch_bookmarked(purpose="tech_news")` |
| 公开数据集、World Bank/Common Crawl | `fetch_bookmarked(purpose="public_data")` |
| 国际新闻、Reuters/Guardian | `fetch_bookmarked(purpose="general_news")` |
| Stack Overflow 技术问答 | `fetch_bookmarked(purpose="technical_qna")` |
| Python 官方文档 | `fetch_bookmarked(purpose="official_doc")` |
| 地缘政治分析 | `fetch_bookmarked(purpose="geo_analysis")` |
| 技术与社会分析 | `fetch_bookmarked(purpose="tech_analysis")` |

## 何时不用

- 用户没有指定具体固定来源 → 用 `web_search`
- 用户给出了具体 URL 而不是用途 → 用 `batch_web_fetch` 或 `web_fetch`

## 书签配置

书签文件 `bookmarks.json` 包含 29 个预置高质量网址，覆盖 12 个用途分类。可按需修改增删。

## 注意事项

- 必须在 `purpose` 中从可用列表精确选择，不能自己编造
- 如果用户需求匹配多个用途，可多次调用或选择最匹配的一个
- 工具会自动按 priority 排序抓取（priority 越高越优先）
- 单个书签抓取失败不影响其他书签
