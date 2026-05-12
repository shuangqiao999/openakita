---
name: news-search
description: Search news using 6 engines in parallel (Bing/Baidu/360/Sogou/Shenma/Toutiao), merged & deduped to top 8 results. Use when you need to find recent news articles, current events, or breaking news.
system: true
handler: web_search
tool-name: news_search
category: Web Search
---

# News Search

六引擎并行搜索新闻 (Bing/百度/360/搜狗/神马/头条)，合并去重取前8条，获取最新资讯。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| query | string | 是 | 搜索关键词 |
| max_results | integer | 否 | 最大结果数（1-20，默认 5） |
| region | string | 否 | 地区代码（wt-wt 全球） |
| safesearch | string | 否 | 安全搜索（on/moderate/off） |
| timelimit | string | 否 | 时间范围（d=一天, w=一周, m=一月） |

## Examples

**搜索新闻**:
```json
{"query": "AI 最新进展", "max_results": 5}
```

**搜索今日新闻**:
```json
{"query": "科技", "timelimit": "d"}
```

## Related Skills

- `web-search`: 搜索网页

