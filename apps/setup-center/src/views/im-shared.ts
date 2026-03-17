// Shared types and constants for IM Bot configuration (used by IMView + IMConfigView)

export type IMBot = {
  id: string;
  type: string;
  name: string;
  agent_profile_id: string;
  enabled: boolean;
  credentials: Record<string, unknown>;
};

export const BOT_TYPES = ["feishu", "telegram", "dingtalk", "wework", "onebot", "qqbot"] as const;

export const BOT_TYPE_LABELS: Record<string, string> = {
  feishu: "飞书",
  telegram: "Telegram",
  dingtalk: "钉钉",
  wework: "企业微信",
  onebot: "OneBot (QQ)",
  qqbot: "QQ 官方机器人",
};

export const CREDENTIAL_FIELDS: Record<string, { key: string; label: string; secret?: boolean }[]> = {
  feishu: [
    { key: "app_id", label: "App ID" },
    { key: "app_secret", label: "App Secret", secret: true },
  ],
  telegram: [
    { key: "bot_token", label: "Bot Token", secret: true },
    { key: "webhook_url", label: "Webhook URL" },
    { key: "proxy", label: "Proxy (http/socks5)" },
    { key: "pairing_code", label: "Pairing Code" },
    { key: "require_pairing", label: "Require Pairing (true/false)" },
  ],
  dingtalk: [
    { key: "client_id", label: "Client ID / App Key" },
    { key: "client_secret", label: "Client Secret / App Secret", secret: true },
  ],
  wework: [
    { key: "corp_id", label: "Corp ID" },
    { key: "token", label: "Token", secret: true },
    { key: "encoding_aes_key", label: "Encoding AES Key", secret: true },
    { key: "callback_port", label: "Callback Port" },
    { key: "callback_host", label: "Callback Host" },
  ],
  onebot: [
    { key: "ws_url", label: "WebSocket URL" },
    { key: "access_token", label: "Access Token", secret: true },
  ],
  qqbot: [
    { key: "app_id", label: "App ID" },
    { key: "app_secret", label: "App Secret", secret: true },
    { key: "sandbox", label: "Sandbox (true/false)" },
    { key: "mode", label: "Mode (websocket/webhook)" },
    { key: "webhook_port", label: "Webhook Port" },
    { key: "webhook_path", label: "Webhook Path" },
  ],
};

export const EMPTY_BOT: IMBot = {
  id: "",
  type: "feishu",
  name: "",
  agent_profile_id: "default",
  enabled: true,
  credentials: {},
};

export const ENABLED_KEY_TO_TYPE: Record<string, string> = {
  TELEGRAM_ENABLED: "telegram",
  FEISHU_ENABLED: "feishu",
  WEWORK_ENABLED: "wework",
  DINGTALK_ENABLED: "dingtalk",
  QQBOT_ENABLED: "qqbot",
  ONEBOT_ENABLED: "onebot",
};

export const TYPE_TO_ENABLED_KEY: Record<string, string> = {
  telegram: "TELEGRAM_ENABLED",
  feishu: "FEISHU_ENABLED",
  wework: "WEWORK_ENABLED",
  dingtalk: "DINGTALK_ENABLED",
  qqbot: "QQBOT_ENABLED",
  onebot: "ONEBOT_ENABLED",
};
