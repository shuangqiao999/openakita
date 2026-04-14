# OpenAkita IM 通道增强修复计划

> 调研时间：2026-04-07 | 项目版本：v1.27.9

---

## 一、总览：当前适配通道 vs 上游最新版本

| 通道 | 当前依赖版本约束 | 上游最新版本 | 差距 | 紧急度 |
|------|-----------------|-------------|------|--------|
| **Telegram** | `python-telegram-bot>=22.0` | **v22.7** (2026-03-16) | 支持至 Bot API 9.5，有破坏性变更 | 🔴 高 |
| **飞书 Feishu** | `lark-oapi>=1.4.0` | **v1.5.3** (2026-01-27) | 1.5.x 有新 API 与修复 | 🟡 中 |
| **钉钉 DingTalk** | `dingtalk-stream>=0.24.3` | **v0.24.3** (2025-10-24) | ✅ 已是最新 | 🟢 低 |
| **企业微信 HTTP** | `aiohttp>=3.9.0` | aiohttp **v3.13.5** (2026-03-31) | 平台侧新增长连接多媒体推送能力 | 🔴 高 |
| **企业微信 WS** | `websockets>=15.0.1` | websockets **v16.0** (2026-01-10) | v16 要求 Python≥3.10，有新特性 | 🟡 中 |
| **OneBot** | `websockets>=15.0.1` | websockets **v16.0** | NapCat 已至 v4.17.55，建议 ID 字段改 str | 🟡 中 |
| **QQ 官方机器人** | `websockets + httpx` (自建) | ✅ 已去 botpy 化 | 自建 WS Gateway + REST，不再依赖 qq-botpy | ✅ 已完成 |
| **WhatsApp 插件** | `@whiskeysockets/baileys ^6.7.0` | **v7.0.0-rc.9** (2025-11) | 7.0 大版本重构，LID 系统全面变更 | 🔴 高 |
| **微信个人号** | httpx + pycryptodome（无独立 extra） | — | pyproject.toml 缺 `wechat` extra 定义 | 🟠 中高 |

### 辅助依赖更新

| 包 | 当前约束 | 最新版本 | 备注 |
|----|---------|---------|------|
| websockets | >=15.0.1 | **16.0** | 要求 Python≥3.10（项目已要求≥3.11，兼容） |
| aiohttp | >=3.9.0 | **3.13.5** | 多项安全修复和性能优化 |
| cryptography | >=42.0.0 | **46.0.6** | CVE-2026-34073 安全修复 |
| pycryptodome | >=3.19.0 | 稳定 | 无重大变化 |

---

## 二、各通道详细分析与修复计划

### 2.1 Telegram — 🔴 高优先级

**上游变更摘要（v22.0 → v22.7）：**

1. **Bot API 9.4 新增**：
   - `VideoQuality` 类 + `Video.qualities` 字段
   - 视频质量控制能力

2. **Bot API 9.5 新增**：
   - `date_time` 消息实体类型（本地化时间显示）
   - `sender_tag` 字段（消息发送者标签）
   - `setChatMemberTag` 方法 + `can_manage_tags` 权限
   - 群组成员标签管理系统

3. **破坏性变更**：
   - 移除 `UniqueGiftInfo.last_resale_star_count`
   - 移除 `Bot.get_business_account_gifts.exclude_limited`
   - `telegram.UniqueGift.gift_id` 从关键字参数改为位置参数

4. **安全更新**：要求 cryptography v46.0.5+、tornado v6.5.5

**修复计划：**

| 序号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| T-1 | 升级版本约束 | `pyproject.toml` | `python-telegram-bot>=22.7` |
| T-2 | 适配 `date_time` 实体 | `telegram.py` | 在消息解析中支持新实体类型，发送消息时支持时间格式化 |
| T-3 | 支持 `sender_tag` | `telegram.py` | 在 `UnifiedMessage` 中携带 sender_tag 信息 |
| T-4 | 视频质量字段 | `telegram.py` | 视频消息接收时解析 `qualities` |
| T-5 | 排查破坏性变更 | `telegram.py` | 检查代码中是否使用了已移除的 API |
| T-6 | 更新 cryptography | `pyproject.toml` | `cryptography>=46.0.5`（CVE 修复） |

---

### 2.2 飞书 Feishu — 🟡 中优先级

**上游变更摘要（v1.4.0 → v1.5.3）：**

- 新增代理支持（Proxy）
- 新增消息服务 & 批量发送功能
- 类型标注修复
- 多项兼容性改进
- 新增 WebSocket 依赖（SDK 自身引入 `websockets`）

**修复计划：**

| 序号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| F-1 | 升级版本约束 | `pyproject.toml` | `lark-oapi>=1.5.0` |
| F-2 | 代理支持适配 | `feishu.py` | 利用 SDK 新增的代理配置能力，统一到 OpenAkita 的代理设置 |
| F-3 | 批量消息能力 | `feishu.py` | 评估并接入批量消息发送 API |
| F-4 | 依赖冲突检查 | — | lark-oapi 1.5.x 自身依赖 `websockets`/`httpx`，需确保与项目其他依赖版本兼容 |

---

### 2.3 钉钉 DingTalk — 🟢 低优先级（已是最新）

当前 `dingtalk-stream>=0.24.3` 已对齐上游最新版本，无需紧急更新。

**可选增强：**

| 序号 | 任务 | 说明 |
|------|------|------|
| D-1 | 监控上游更新 | 关注 open-dingtalk/dingtalk-stream-sdk-python 的新 Release |
| D-2 | 卡片消息增强 | 评估钉钉互动卡片（AI Card）的最新模板能力 |

---

### 2.4 企业微信 — 🔴 高优先级

**重要：企业微信 2026-03 新增长连接多媒体能力**

企业微信官方在 2026 年 3 月密集更新了智能机器人 API：
- ✅ 长连接模式支持推送**图片、语音、视频和文件**
- ✅ 智能机器人支持**主动推送消息**
- ✅ 智能机器人支持调用**文档 MCP 工具**接口
- ✅ 应用发消息到群聊支持 **@群成员** 和 **@所有人**
- ✅ Markdown 消息支持 **@功能**

**官方 SDK 出现：**
- `wecom-aibot-python-sdk` v1.0.2（官方 Python SDK，基于 asyncio + WebSocket）
- 支持：文本/图片/混合/语音/文件/视频消息、流式回复、模板卡片、主动推送、文件下载解密

**修复计划：**

| 序号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| W-1 | WS 适配器补全多媒体推送 | `wework_ws.py` | 确认图片/语音/视频/文件的**主动推送**是否已完整实现 |
| W-2 | 评估官方 SDK 替代 | 整体 | 评估是否将 `wework_ws.py` 的自实现替换为 `wecom-aibot-python-sdk`，降低维护成本 |
| W-3 | @群成员能力 | `wework_bot.py` / `wework_ws.py` | 接入 Markdown 消息 @ 功能 |
| W-4 | MCP 工具接口 | `wework_ws.py` | 评估接入企业微信文档 MCP 工具能力 |
| W-5 | 更新 websockets 约束 | `pyproject.toml` | `websockets>=16.0`（新特性 + 安全修复） |
| W-6 | 更新 cryptography | `pyproject.toml` | `cryptography>=46.0.5`（CVE 修复） |
| W-7 | 更新 aiohttp | `pyproject.toml` | `aiohttp>=3.13.0`（多项安全修复） |

---

### 2.5 OneBot — 🟡 中优先级

**上游生态变更：**

- **NapCat** 已更新至 v4.17.55，推荐使用字符串类型的 `message_id`/`user_id`/`group_id`
- **OneBot v12** 协议已成熟，统一了 `send_message` 等 API 命名，支持 Channel/Guild
- NapCat 和 Lagrange 目前仍以 OneBot v11 为主

**修复计划：**

| 序号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| O-1 | ID 字段字符串化 | `onebot.py` | NapCat v4.8.115+ 推荐 `user_id`/`group_id` 使用 `str`，避免大整数精度丢失 |
| O-2 | 评估 OneBot v12 支持 | `onebot.py` | 在现有 v11 基础上，增加 v12 协议协商能力 |
| O-3 | 更新 websockets | `pyproject.toml` | 与企业微信 WS 通道统一升至 `>=16.0` |

---

### 2.6 QQ 官方机器人 — 🟠 中高优先级

**现状问题：**

- `qq-botpy` v1.2.1 发布于 **2024-03**，GitHub 最后更新 2024-09，已超 1.5 年未更新
- 53 个未关闭 Issue，社区活跃度下降
- QQ 官方 API 可能已有新变更但 SDK 未跟进

**修复计划：**

| 序号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| Q-1 | 兼容性测试 | `qq_official.py` | 验证当前 botpy v1.2.1 与 QQ 最新 API 的兼容性 |
| Q-2 | 评估替代方案 | — | 考虑：(a) Fork 维护 botpy; (b) 直接调用 HTTP API 去 SDK 化; (c) 通过 NapCat/OneBot 间接接入 |
| Q-3 | Webhook 模式健壮性 | `qq_official.py` | 加强 Webhook 模式下的签名验证和错误处理 |

---

### 2.7 WhatsApp 插件 — 🔴 高优先级

**Baileys 7.0 重大破坏性变更：**

1. **LID（Local Identifier）系统替代手机号**：
   - Auth state 需新增 `lid-mapping`、`device-list`、`tctoken` key
   - `Contact` 类型字段重构：去掉 `jid`/`lid`，改为 `id`/`phoneNumber`/`lid`
   - `isJidUser()` 被移除，替换为 `isPnUser()`
   - `onWhatsApp()` 不再返回 LID，需使用 `getLIDForPN()`

2. **运行时要求**：Node.js >= 20.0.0

3. **Socket 配置变更**：
   - `Browsers` 导入路径变更
   - `getMessage` 回调现在是**必需**的

4. **ACK 系统变更**：不再自动发送 ACK（防封措施）

**修复计划：**

| 序号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| WA-1 | 升级 Baileys | `bridge/package.json` | `@whiskeysockets/baileys` 升至 `^7.0.0` |
| WA-2 | LID 系统迁移 | `bridge/index.js` | 全面适配 LID 身份标识系统 |
| WA-3 | Auth state 更新 | `bridge/index.js` | 新增 `lid-mapping`/`device-list`/`tctoken` 存储 |
| WA-4 | Contact 模型适配 | `plugin.py` + `bridge/index.js` | 更新联系人字段解析逻辑 |
| WA-5 | 必需 getMessage | `bridge/index.js` | 实现 `getMessage` 回调 |
| WA-6 | Node.js 版本要求 | `plugin.json` / 文档 | 声明 Node.js >= 20 要求 |
| WA-7 | ACK 行为适配 | `bridge/index.js` | 理解新 ACK 策略，调整消息确认逻辑 |

---

### 2.8 微信个人号（iLink Bot API）— 🟠 中高优先级

**现状问题：**

- `wechat` 适配器存在于 `adapters/wechat.py`，配置字段在 `config.py` 中定义
- `channels/deps.py` 将 `wechat` 映射到 `openakita[wechat]`
- **但 `pyproject.toml` 中并未定义 `wechat` 这个 optional-dependencies extra**
- 实际依赖是 `httpx`（核心自带）+ `pycryptodome`（需手动安装或通过 `wework` extra 获得）

**修复计划：**

| 序号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| WC-1 | 补全 `wechat` extra | `pyproject.toml` | 添加 `wechat = ["pycryptodome>=3.19.0"]` |
| WC-2 | 同步到 `all` extra | `pyproject.toml` | 在 `all` 中加入 wechat 相关依赖 |
| WC-3 | 更新文档 | `.env.example` / docs | 确保安装文档与配置一致 |

---

## 三、pyproject.toml 依赖版本修订汇总

```toml
# 核心依赖变更
"python-telegram-bot>=22.7",     # was >=22.0

# 可选依赖变更
[project.optional-dependencies]
feishu = [
    "lark-oapi>=1.5.0",          # was >=1.4.0
    "qrcode>=8.0",
]

wework = [
    "aiohttp>=3.13.0",           # was >=3.9.0 — 安全修复
    "pycryptodome>=3.19.0",
]

wework_ws = [
    "websockets>=16.0",          # was >=15.0.1 — 新特性 + Python 3.10 要求
    "cryptography>=46.0.5",      # was >=42.0.0 — CVE-2026-34073 修复
]

onebot = [
    "websockets>=16.0",          # was >=15.0.1
]

# 新增 wechat extra（修复配置不一致）
wechat = [
    "pycryptodome>=3.19.0",
]
```

---

## 四、执行优先级路线图

### 第一阶段：紧急修复（1-2 周）

| 编号 | 任务 | 理由 |
|------|------|------|
| WC-1/2/3 | 补全 `wechat` extra 定义 | 配置不一致，用户无法正常安装 |
| T-1/T-5/T-6 | Telegram 版本升级 + 破坏性变更排查 + 安全更新 | 有 Breaking Change 和 CVE |
| W-5/W-6/W-7 | 企业微信辅助依赖安全更新 | CVE-2026-34073 (cryptography) |

### 第二阶段：功能增强（2-4 周）

| 编号 | 任务 | 理由 |
|------|------|------|
| T-2/T-3/T-4 | Telegram Bot API 9.4/9.5 新功能适配 | 新消息实体类型、标签系统 |
| W-1/W-3 | 企业微信长连接多媒体 + @能力 | 平台 2026-03 重大更新 |
| F-1/F-2 | 飞书 SDK 升级 + 代理支持 | 新版本改进 |
| O-1 | OneBot ID 字段字符串化 | NapCat 兼容性 |

### 第三阶段：架构升级（4-8 周）

| 编号 | 任务 | 理由 |
|------|------|------|
| WA-1~7 | WhatsApp Baileys 7.0 全面迁移 | 大版本重构，需全面测试 |
| W-2 | 评估企业微信官方 SDK 替代自实现 | 降低维护成本 |
| Q-2 | QQ 机器人 SDK 替代方案评估 | botpy 长期停更风险 |
| O-2 | OneBot v12 协议支持评估 | 生态升级方向 |
| W-4 | 企业微信 MCP 工具接口 | 前沿能力探索 |

---

## 五、风险评估

| 风险项 | 影响 | 缓解措施 |
|--------|------|---------|
| Baileys 7.0 LID 迁移复杂度高 | WhatsApp 通道可能需大幅重写 | 建立独立分支，充分测试后合并 |
| qq-botpy 长期停更 | QQ 通道可能出现兼容性问题 | 准备 HTTP API 直调 fallback 方案 |
| websockets 16.0 要求 Python≥3.10 | 项目已要求≥3.11，无影响 | ✅ 已满足 |
| lark-oapi 1.5 内部依赖 websockets | 可能与项目现有 websockets 版本冲突 | 需进行依赖解析测试 |
| 企业微信官方 SDK 仍为 Beta | 生产稳定性待验证 | 可保留自实现作为 fallback |

---

## 六、参考链接

- [python-telegram-bot v22.7 Changelog](https://docs.python-telegram-bot.org/en/latest/changelog.html)
- [Telegram Bot API 9.5](https://core.telegram.org/bots/api-changelog)
- [lark-oapi v1.5.3 PyPI](https://pypi.org/project/lark-oapi/1.5.3/)
- [dingtalk-stream GitHub](https://github.com/open-dingtalk/dingtalk-stream-sdk-python)
- [企业微信更新日志](https://developer.work.weixin.qq.com/document/path/93221)
- [wecom-aibot-python-sdk GitHub](https://github.com/WecomTeam/wecom-aibot-python-sdk)
- [Baileys 7.0 迁移指南](https://baileys.wiki/docs/migration/to-v7.0.0/)
- [NapCat GitHub](https://github.com/NapNeko/NapCatQQ)
- [websockets 16.0 Changelog](https://websockets.readthedocs.io/en/stable/project/changelog.html)
- [cryptography 46.0.6 CVE-2026-34073](https://cryptography.io/en/stable/changelog/)
- [aiohttp 3.13.5 Changelog](https://docs.aiohttp.org/en/stable/changes.html)
