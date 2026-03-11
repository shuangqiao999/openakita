# 企业微信 WebSocket 长连接适配器 — 功能清单 / 协议约束 / 已知限制

> 本文档记录企业微信 WebSocket 长连接适配器（`wework_ws.py`）的功能、协议细节和与其他模块的交互逻辑。
> 目的：后续修改或修 bug 时不遗漏既有逻辑约束。

---

## 一、核心功能清单

### 1. 消息接收

| 功能 | 关键代码位置 | 说明 |
|------|------------|------|
| WebSocket 长连接 | `_connection_loop()` | 主动连接 `wss://openws.work.weixin.qq.com` |
| 认证 | `_send_auth()` | 连接后立即发认证帧 (`aibot_subscribe`) |
| 心跳保活 | `_heartbeat_loop()` | 30s 间隔，连续 2 次无回复判定连接死亡 |
| 指数退避重连 | `_connection_loop()` | 1s → 2s → 4s → ... → 30s (cap)，默认无限重连 |
| 消息类型解析 | `_parse_content()` | 支持 text/image/mixed/voice/file |
| 事件类型解析 | `_handle_event_callback()` | enter_chat/template_card_event/feedback_event |
| 消息去重 | `_seen_msg_ids` (OrderedDict) | 按 `msgid` 去重，上限 500 |

### 2. 消息发送

| 功能 | 方法 | 说明 |
|------|------|------|
| 流式文本回复 | `_send_stream_reply()` | `msgtype: "stream"`，透传 `req_id`，自动分片 (20480B) |
| 图片回复 | `_prepare_image_items()` | base64 编码，仅在 `finish=true` 时附加，最多 10 张 |
| 主动推送 (Markdown) | `_send_active_message()` | `cmd: "aibot_send_msg"`，自己生成 `req_id` |
| response_url 回退 | `_response_url_fallback()` | WS 回复失败时通过 HTTP POST 回退 |
| Webhook 图片发送 | `_WebhookSender.send_image()` | base64+md5 直发，需配置 `wework_ws_webhook_url` |
| Webhook 语音发送 | `_WebhookSender.send_voice()` | 非 AMR 先转 AMR → upload_media → 发送 |
| Webhook 文件发送 | `_WebhookSender.send_file()` | upload_media → 发送 |

### 3. 文件处理

| 功能 | 方法 | 说明 |
|------|------|------|
| 文件下载 | `download_media()` | httpx GET，从 Content-Disposition 解析文件名 |
| AES-256-CBC 解密 | `_decrypt_file()` | per-file aeskey (base64)，iv=key[:16]，PKCS#7 pad 32B block |
| 上传 | `upload_media()` | 不支持（图片通过 base64 内联发送） |

### 4. 启动流程

| 步骤 | 说明 |
|------|------|
| `start()` | 导入 `websockets`，创建 `_connection_task` |
| `_connection_loop()` | 循环：连接 → 认证 → 心跳+接收 → 断开 → 退避重连 |
| `_connect_and_run()` | 单次连接生命周期 |
| `_send_auth()` | 发送 `{cmd: "aibot_subscribe", body: {bot_id, secret}}` |
| 等待认证响应 | 10s 超时，`errcode=0` 才启动心跳 |
| `_heartbeat_loop()` | 定时 ping，维持连接 |
| `_receive_loop()` | 读帧 → `_route_frame()` 分发 |

---

## 二、WebSocket 协议细节

### 通用帧格式

```json
{
  "cmd": "string | undefined",
  "headers": { "req_id": "prefix_timestamp_random8hex", ... },
  "body": { ... },
  "errcode": 0,
  "errmsg": "ok"
}
```

### 所有 cmd 值

| 方向 | cmd | 用途 |
|------|-----|------|
| 客户端 → 服务端 | `aibot_subscribe` | 认证订阅 |
| 客户端 → 服务端 | `ping` | 心跳 |
| 客户端 → 服务端 | `aibot_respond_msg` | 回复消息 |
| 客户端 → 服务端 | `aibot_respond_welcome_msg` | 回复欢迎语 |
| 客户端 → 服务端 | `aibot_respond_update_msg` | 更新模板卡片 |
| 客户端 → 服务端 | `aibot_send_msg` | 主动推送消息 |
| 服务端 → 客户端 | `aibot_msg_callback` | 消息推送 |
| 服务端 → 客户端 | `aibot_event_callback` | 事件推送 |

### 帧路由优先级 (`_route_frame`)

1. `cmd = "aibot_msg_callback"` → 消息处理
2. `cmd = "aibot_event_callback"` → 事件处理
3. 无 cmd + req_id 在 `_pending_acks` → 回复回执
4. 无 cmd + req_id 以 `aibot_subscribe` 开头 → 认证响应
5. 无 cmd + req_id 以 `ping` 开头 → 心跳响应
6. 其他 → 日志记录

### 回复规则

- **回复消息**：透传收到消息的 `req_id`
- **主动推送**：自己生成 `req_id`（前缀 `aibot_send_msg`）
- **串行队列**：同一 `req_id` 的回复串行发送，每条等回执后才发下一条
- **回执超时**：5 秒
- **流式内容上限**：20480 字节/片（UTF-8），自动分片

---

## 三、与 HTTP 回调适配器的对比

| 特性 | HTTP 回调 (`wework_bot.py`) | WebSocket (`wework_ws.py`) |
|------|---------------------------|---------------------------|
| 连接方式 | 被动 HTTP 服务器 | 主动 WebSocket 客户端 |
| 需要公网 | 是（回调地址） | 否（出站连接即可） |
| 认证方式 | corp_id + token + encoding_aes_key | bot_id + secret |
| 消息加密 | AES-256-CBC (全局 encoding_aes_key) | 无加密（WSS 传输层加密） |
| 文件解密 | 全局 encoding_aes_key | per-file aeskey |
| 流式回复 | 支持（HTTP 轮询刷新） | 原生支持（WebSocket stream） |
| 主动推送 | 通过 response_url | 通过 `aibot_send_msg` cmd |
| 心跳/重连 | 不需要 | 30s 心跳，指数退避重连 |
| `supports_streaming` | False | True |

---

## 四、关键逻辑约束（修改时必须保持）

### 约束 1：req_id 透传规则

- **回复消息**时必须使用收到消息帧中的 `req_id`（不能重新生成）
- **主动推送**时必须使用自己生成的 `req_id`（前缀 `aibot_send_msg`）
- 服务端通过 `req_id` 关联消息与回复

### 约束 2：流式回复串行性

- 同一 `req_id` 的多个 stream 帧必须串行发送
- 每发一帧必须等待服务端回执（`errcode: 0`）后才能发下一帧
- 不能并行发送同一 `req_id` 的帧（会导致消息乱序或丢失）

### 约束 3：图片只能在 finish=true 时发送

- `stream.msg_item`（base64 图片）只在 `finish=true` 的最后一帧有效
- 最多 10 张图片
- 图片以 base64 编码内联发送（非 URL，非文件上传）

### 约束 4：心跳超时判定

- 发心跳**之前**检查 `missed_pong` 计数
- 收到心跳响应时重置为 0
- 连续 2 次未收到 → `ws.close()` → 触发重连
- 不能在发心跳**之后**才检查（否则错过 1 个周期）

### 约束 5：认证必须在连接后立即发送

- WebSocket `open` 后第一帧必须是认证帧
- 认证超时 10 秒
- 认证失败不启动心跳，直接断开进入重连

### 约束 6：is_mentioned 的平台特性

- WebSocket 模式下，企业微信**只推送**涉及机器人的消息
- 因此所有收到的消息 `is_mentioned = True`（平台已预过滤）
- 与 HTTP 回调模式不同（HTTP 收到所有群消息，需要自己判断 is_mentioned）

### 约束 7：response_url 的生命周期

- 每条消息附带 `response_url`，有效期约 5 分钟
- 缓存在 `_response_urls` dict 中（按 req_id 索引）
- WS 回复失败时作为 HTTP POST 回退
- 定期清理，保留最近 200 条

---

## 五、配置说明

### .env 配置

```ini
# 企业微信 WebSocket 长连接模式
WEWORK_WS_ENABLED=true
WEWORK_WS_BOT_ID=your_bot_id
WEWORK_WS_SECRET=your_bot_secret

# 可选：群机器人 Webhook URL（用于发送图片/语音/文件）
# 在企业微信群设置 → 群机器人 → 添加机器人 → 获取 Webhook 地址
WEWORK_WS_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

### im_bots JSON 配置（多 Bot 模式）

```json
{
  "type": "wework_ws",
  "bot_id": "your_bot_id",
  "secret": "your_bot_secret",
  "ws_url": "wss://openws.work.weixin.qq.com",
  "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
}
```

### 注意事项

- HTTP 回调模式和 WebSocket 模式可以**同时启用**（不同的 bot_id）
- WebSocket 模式**不需要公网 IP**，适合开发和内网部署
- `bot_id` 和 `secret` 在企业微信管理后台的智能机器人配置页面获取

---

## 六、数据流概览

### 消息接收流程

```
企业微信 WebSocket 服务端
  → JSON 帧: {cmd: "aibot_msg_callback", headers: {req_id}, body: {msgid, ...}}
    → _receive_loop()              # async for msg in ws
      → _route_frame()             # 按 cmd 分发
        → _handle_msg_callback()   # 消息去重 + 解析
          → _parse_content()       # text/image/mixed/voice/file → MessageContent
          → UnifiedMessage.create()
          → _emit_message()        # 触发 gateway 回调
```

### 消息发送流程 (回复)

```
Agent 生成回复
  → gateway 构造 OutgoingMessage (metadata.req_id = 收到消息的 req_id)
    → adapter.send_message()
      → _send_stream_reply()
        → 分片 (每片 ≤ 20480 字节)
        → for each chunk:
            → _send_reply_with_ack(req_id, body, "aibot_respond_msg")
              → ws.send(frame)
              → await ack (5s timeout)
        → 最后一片: finish=true + img_items
```

### 消息发送流程 (主动推送)

```
Agent 主动发送
  → gateway 构造 OutgoingMessage (无 req_id)
    → adapter.send_message()
      → _send_active_message()
        → _send_reply_with_ack(自生成 req_id, body, "aibot_send_msg")
```

### 连接生命周期

```
start()
  → _connection_loop() [asyncio.Task]
    → while running:
        → _connect_and_run()
          → websockets.connect()
          → _send_auth()
          → wait authenticated (10s)
          → asyncio.gather:
              → _heartbeat_loop() [每 30s ping]
              → _receive_loop()   [读帧]
        → on disconnect:
          → 指数退避 (1s, 2s, 4s, ... 30s cap)
          → retry
```

---

## 七、已知限制（后续迭代处理）

| # | 严重度 | 问题 | 说明 |
|---|--------|------|------|
| 1 | 中 | 模板卡片回复未完整实现 | 已预留 cmd 常量，但 `send_message` 尚未支持构建模板卡片；需要 `OutgoingMessage` 扩展 |
| 2 | 中 | 欢迎语回复未自动化 | `enter_chat` 事件已上报 Gateway，但需要 Gateway 层支持 5s 内自动回复欢迎语 |
| 3 | 中 | 更新模板卡片未实现 | `aibot_respond_update_msg` cmd 已预留，等待业务需求 |
| 4 | 低 | 流式回复中断无恢复 | 连接断开时进行中的 stream 直接标记失败，尝试 response_url 回退 |
| 5 | 低 | response_url 缓存无 TTL | 仅按数量清理（200 条），未按时间清理过期 URL |
| 6 | 低 | 引用消息 (quote) 未解析 | 消息体中的 `quote` 字段已在 raw 中保留，但未映射到 `reply_to` |
| 7 | 低 | ~~语音消息只取转文字结果~~ (已优化) | `voice.content` 是企业微信自动转写的文字，原始音频不可获取。已统一 WS/Bot 输出格式：转写成功→直接用文本（无前缀），失败→`[语音消息，平台未能识别，请重新发送或改用文字]` |

---

## 八、修改检查清单

修改企业微信 WebSocket 适配器相关代码时，请逐一确认：

- [ ] 回复消息是否透传了原始 `req_id`（而非重新生成）？
- [ ] 主动推送是否使用了自己生成的 `req_id`（前缀 `aibot_send_msg`）？
- [ ] 同一 `req_id` 的回复是否保持串行（经过 `_reply_locks`）？
- [ ] 流式回复的每一片是否等待了回执？
- [ ] 图片 `msg_item` 是否只在 `finish=true` 时附加？
- [ ] 新增的事件类型是否在 `_handle_event_callback` 中处理？
- [ ] 心跳超时判定是否在发送前检查？
- [ ] `_reject_all_pending` 是否在断开/重连时调用？
- [ ] `is_mentioned` 是否保持为 True（平台已预过滤）？
- [ ] 语音输出格式是否与 `wework_bot.py` 保持一致（转写成功→直接文本，失败→统一提示）？
- [ ] `_WebhookSender` 是否在 `stop()` 时关闭 httpx 客户端？
- [ ] 新增的媒体发送方法是否优先尝试 webhook 通道？

---

## 九、协议参考

- 官方文档: https://open.work.weixin.qq.com/help2/pc/cat?doc_id=21657
- SDK 源码: https://github.com/WecomTeam/aibot-node-sdk (MIT)
- 本适配器参考 SDK 协议规范独立实现，非代码翻译
