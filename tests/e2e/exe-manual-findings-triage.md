# EXE 人工测试问题登记与原因初判

> 来源: 用户人工测试 32 条反馈 + 工作区日志  
> 日志根目录: `C:\Users\Peilong_Hong\.openakita\workspaces\default`

---

## A. 已有日志证据（优先修）

1) **默认英文回复（中文语境漂移）**  
- 现象: 发送中文问候，回复为 `Hello! 👋 I'm OpenAkita...`  
- 初判: 历史消息中英文模板回复被反复带入上下文，模型沿用英文应答风格。  
- 证据:
  - `C:\Users\Peilong_Hong\.openakita\workspaces\default\data\llm_debug\llm_request_20260402_092018_46b7a25a.json`
  - `C:\Users\Peilong_Hong\.openakita\workspaces\default\data\llm_debug\llm_response_20260402_091910_a1820f35.json`

2) **工具调用文本泄漏（`<tool_call>...</tool_call>` 原样显示）**  
- 现象: 找新闻/图片时，界面直接显示 tool_call 文本，无后续执行结果。  
- 初判: 工具调用没有走结构化 tool event 管道，而被当普通文本回复。  
- 证据:
  - `C:\Users\Peilong_Hong\.openakita\workspaces\default\data\llm_debug\llm_response_20260402_092022_46b7a25a.json`
  - `C:\Users\Peilong_Hong\.openakita\workspaces\default\data\llm_debug\llm_response_20260402_091857_35374189.json`
  - `C:\Users\Peilong_Hong\.openakita\workspaces\default\logs\openakita.log`（`[Chat API] 回复完成` 中可见 `<tool_call>...`）

3) **LLM 结构错误与超时（影响流式和稳定性）**  
- 现象: 偶发慢、中断、无流式或直接失败。  
- 初判: 上游端点在部分请求触发 `content field is required` + `ReadTimeout`。  
- 证据:
  - `C:\Users\Peilong_Hong\.openakita\workspaces\default\logs\error.log`
  - `C:\Users\Peilong_Hong\.openakita\workspaces\default\logs\openakita-serve.log`

4) **`/clear` 后仍可记住会话内容**  
- 现象: 清空后仍记得“上个会话让它查新闻/昵称信息”。  
- 初判: `chat/clear` 与内存会话索引/会话键映射存在不一致，清理未命中当前会话。  
- 证据:
  - `C:\Users\Peilong_Hong\.openakita\workspaces\default\logs\openakita.log`（`exe_clear_6029391e` 相关记录）
  - `C:\Users\Peilong_Hong\.openakita\workspaces\default\logs\openakita-serve.log`

5) **`/skills` 与 `/memory` 行为异常（前端提示与后端实际不一致）**  
- 现象: 前端提示“暂无已安装技能/无法加载记忆”，但后端有技能与记忆。  
- 初判: 前端展示层与后端命令执行返回格式不一致（尤其 tool_call 文本泄漏时），导致 UI 判定失败。  
- 证据:
  - 技能已加载日志: `C:\Users\Peilong_Hong\.openakita\workspaces\default\logs\openakita.log`
  - `/skills` 回包出现 tool_call 文本（复现实验）

6) **“你好/你是谁”回复变快的机制说明**  
- 结论: **不是简单关键词匹配**。  
- 初判: 这类请求多走 `chat/other` 轻路径、无需工具、迭代轮次低，响应自然更快。  
- 证据:
  - `C:\Users\Peilong_Hong\.openakita\workspaces\default\logs\openakita.log`（`Intent: chat, task_type: other`）

---

## B. 主要前端/UI 问题（目前日志证据不足，需前端埋点）

下列问题你反馈稳定复现，但当前 `frontend.log` 几乎只有启动行，缺少行为级埋点，建议先补日志：

- 消息不自动滚动到底（发送后/切页返回后）
- 搜索栏不高亮（跳转与关闭有效）
- 无流式视觉效果
- `/` 命令展示样式异常、消息容器不可完整滚动
- 对话回卷缺确认弹窗
- 语音录制无“录制中动画”
- 拖拽上传大文件卡死（按钮上传会报错，拖拽无报错）
- 图片预览无法打开（因图片工具链路失败导致无法进入预览）
- 上下文用量可视化缺失
- 安全确认弹窗无法触发
- `@ Agent` 中文联想弱、键盘下移不带动列表滚动
- `Ctrl+Shift+Z` Redo 无效
- 大文本粘贴无“展开预览”
- 导出按钮无反应
- 全局快捷键无反应
- 托盘图标无状态颜色
- 设置变更重启提示未出现
- 工具 loading 卡片无法验证

`frontend.log` 现状证据:
- `C:\Users\Peilong_Hong\.openakita\workspaces\default\logs\frontend.log`（仅启动记录）

---

## C. 建议的修复优先级（按影响）

- **P1**: 工具调用文本泄漏、LLM 400/超时、`/clear` 失效、无流式效果  
- **P2**: 自动滚动、上传卡死、导出无响应、全局快捷键、设置重启提示  
- **P3**: UI 细节（高亮、提示文案、动画、键盘滚动体验）

---

## D. 先补哪些前端埋点（便于下一轮精准修）

建议在前端加这些统一日志事件（写入 `frontend.log`）：

- `chat.stream.start` / `chat.stream.delta` / `chat.stream.done`
- `chat.auto_scroll.trigger` / `chat.auto_scroll.skip(reason)`
- `slash.command.input` / `slash.command.execute` / `slash.command.render_error`
- `upload.drag.start` / `upload.drag.progress` / `upload.drag.error`
- `export.start` / `export.success` / `export.error`
- `shortcut.global.trigger` / `shortcut.global.register_error`
- `memory.load.start` / `memory.load.error` / `skills.load.error`

