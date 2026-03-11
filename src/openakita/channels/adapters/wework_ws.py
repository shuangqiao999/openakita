"""
企业微信智能机器人 WebSocket 长连接适配器

基于企业微信智能机器人 WebSocket 协议实现:
- WebSocket 长连接 (wss://openws.work.weixin.qq.com)
- 认证 / 心跳 / 指数退避重连
- 消息接收 (text/image/mixed/voice/file)
- 流式回复 (stream) / 模板卡片 / 主动推送
- 文件下载 + AES-256-CBC 逐文件解密
- response_url HTTP 回退

WebSocket protocol referenced from @wecom/aibot-node-sdk (MIT)
https://github.com/WecomTeam/aibot-node-sdk
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..base import ChannelAdapter
from ..types import (
    MediaFile,
    MediaStatus,
    MessageContent,
    OutgoingMessage,
    UnifiedMessage,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 延迟导入
# ---------------------------------------------------------------------------
websockets: Any = None
httpx: Any = None


def _import_websockets():
    global websockets
    if websockets is None:
        try:
            import websockets as ws
            websockets = ws
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("websockets"))


def _import_httpx():
    global httpx
    if httpx is None:
        try:
            import httpx as hx
            httpx = hx
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("httpx"))


# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------
WS_DEFAULT_URL = "wss://openws.work.weixin.qq.com"

CMD_SUBSCRIBE = "aibot_subscribe"
CMD_HEARTBEAT = "ping"
CMD_RESPONSE = "aibot_respond_msg"
CMD_RESPONSE_WELCOME = "aibot_respond_welcome_msg"
CMD_RESPONSE_UPDATE = "aibot_respond_update_msg"
CMD_SEND_MSG = "aibot_send_msg"
CMD_CALLBACK = "aibot_msg_callback"
CMD_EVENT_CALLBACK = "aibot_event_callback"

STREAM_CONTENT_MAX_BYTES = 20480

# msg_item base64 image limit (~300KB base64 ≈ 225KB raw).
# WeChat Work silently discards oversized msg_item payloads.
MSG_ITEM_IMAGE_MAX_BYTES = 200 * 1024
MSG_ITEM_IMAGE_MAX_WIDTH = 1920


# ---------------------------------------------------------------------------
# req_id 生成
# ---------------------------------------------------------------------------
def _generate_req_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(4)}"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
@dataclass
class WeWorkWsConfig:
    """企业微信 WebSocket 适配器配置"""
    bot_id: str
    secret: str
    ws_url: str = WS_DEFAULT_URL
    heartbeat_interval: float = 30.0
    max_missed_pong: int = 2
    max_reconnect_attempts: int = -1
    reconnect_base_delay: float = 1.0
    reconnect_max_delay: float = 30.0
    reply_ack_timeout: float = 5.0
    max_reply_queue_size: int = 100


# ---------------------------------------------------------------------------
# AES-256-CBC 文件解密 (per-file aeskey)
# ---------------------------------------------------------------------------
def _decrypt_file(encrypted: bytes, aes_key_b64: str) -> bytes:
    """解密企业微信文件 (AES-256-CBC, PKCS#7 pad to 32-byte block)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = base64.b64decode(aes_key_b64)
    if len(key) != 32:
        raise ValueError(f"AES key must be 32 bytes, got {len(key)}")
    iv = key[:16]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(encrypted) + decryptor.finalize()
    # PKCS#7 unpad (block_size=32)
    if not decrypted:
        raise ValueError("Decrypted data is empty")
    pad_len = decrypted[-1]
    if pad_len < 1 or pad_len > 32 or pad_len > len(decrypted):
        raise ValueError(f"Invalid PKCS#7 padding value: {pad_len}")
    for i in range(len(decrypted) - pad_len, len(decrypted)):
        if decrypted[i] != pad_len:
            raise ValueError("Invalid PKCS#7 padding: bytes mismatch")
    return decrypted[: len(decrypted) - pad_len]


# ---------------------------------------------------------------------------
# Webhook 辅助发送器（图片/语音/文件）
# ---------------------------------------------------------------------------
class _WebhookSender:
    """通过企微群机器人 Webhook URL 发送富媒体消息（图片/语音/文件）。

    WS 长连接的 aibot_respond_msg 只支持文本 stream + msg_item 图片，
    且 msg_item 在当前版本不一定渲染。此类通过群 Webhook 实现可靠的
    图片/语音/文件发送，作为辅助通道。

    Webhook API 参考:
    https://developer.work.weixin.qq.com/document/path/91770
    """

    def __init__(self, webhook_url: str):
        from urllib.parse import parse_qs, urlparse

        self._send_url = webhook_url
        parsed = urlparse(webhook_url)
        self._key = parse_qs(parsed.query).get("key", [""])[0]
        self._upload_url = (
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media"
            f"?key={self._key}&type={{media_type}}"
        )
        self._client: Any = None

    async def _ensure_client(self):
        if self._client is None or self._client.is_closed:
            _import_httpx()
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self._client

    async def send_image(self, image_path: str) -> bool:
        """发送图片（base64 + md5，无需上传）。"""
        try:
            path = Path(image_path)
            if not path.exists():
                logger.warning(f"[WebhookSender] Image not found: {image_path}")
                return False
            data = path.read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            md5 = hashlib.md5(data).hexdigest()
            payload = {
                "msgtype": "image",
                "image": {"base64": b64, "md5": md5},
            }
            return await self._post(payload)
        except Exception as e:
            logger.error(f"[WebhookSender] send_image failed: {e}")
            return False

    async def send_voice(self, voice_path: str) -> bool:
        """发送语音（需先转 AMR 再 upload_media 获取 media_id）。"""
        try:
            amr_path = await self._ensure_amr(voice_path)
            media_id = await self._upload_media(amr_path, "voice")
            if not media_id:
                return False
            payload = {
                "msgtype": "voice",
                "voice": {"media_id": media_id},
            }
            return await self._post(payload)
        except Exception as e:
            logger.error(f"[WebhookSender] send_voice failed: {e}")
            return False

    async def send_file(self, file_path: str) -> bool:
        """发送文件（upload_media 获取 media_id）。"""
        try:
            media_id = await self._upload_media(file_path, "file")
            if not media_id:
                return False
            payload = {
                "msgtype": "file",
                "file": {"media_id": media_id},
            }
            return await self._post(payload)
        except Exception as e:
            logger.error(f"[WebhookSender] send_file failed: {e}")
            return False

    async def _upload_media(self, file_path: str, media_type: str) -> str | None:
        """上传媒体文件到企微获取 media_id。"""
        client = await self._ensure_client()
        url = self._upload_url.format(media_type=media_type)
        path = Path(file_path)
        try:
            files = {"media": (path.name, path.read_bytes())}
            resp = await client.post(url, files=files)
            result = resp.json()
            if result.get("errcode", 0) != 0:
                logger.error(
                    f"[WebhookSender] upload_media failed: "
                    f"{result.get('errcode')} {result.get('errmsg')}"
                )
                return None
            media_id = result.get("media_id", "")
            logger.info(f"[WebhookSender] Uploaded {media_type}: {path.name} → {media_id[:20]}...")
            return media_id
        except Exception as e:
            logger.error(f"[WebhookSender] upload_media error: {e}")
            return None

    async def _ensure_amr(self, voice_path: str) -> str:
        """确保语音文件为 AMR 格式（Webhook voice 要求 AMR）。"""
        path = Path(voice_path)
        if path.suffix.lower() == ".amr":
            return voice_path

        amr_path = str(path.with_suffix(".amr"))
        if Path(amr_path).exists() and Path(amr_path).stat().st_size > 0:
            return amr_path

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", voice_path,
            "-ar", "8000", "-ac", "1", "-ab", "12.2k",
            "-y", amr_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            logger.error(f"[WebhookSender] AMR conversion failed: {stderr[:300]}")
            raise RuntimeError(f"AMR conversion failed for {path.name}")
        logger.info(f"[WebhookSender] Converted to AMR: {path.name}")
        return amr_path

    async def _post(self, payload: dict) -> bool:
        """发送 Webhook 请求。"""
        client = await self._ensure_client()
        try:
            resp = await client.post(self._send_url, json=payload)
            result = resp.json()
            if result.get("errcode", 0) != 0:
                logger.error(
                    f"[WebhookSender] Post failed: "
                    f"{result.get('errcode')} {result.get('errmsg')}"
                )
                return False
            return True
        except Exception as e:
            logger.error(f"[WebhookSender] Post error: {e}")
            return False

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------
class WeWorkWsAdapter(ChannelAdapter):
    """
    企业微信智能机器人 WebSocket 长连接适配器

    通过 WebSocket 与企业微信服务端保持长连接，实现:
    - 消息接收 (text/image/mixed/voice/file)
    - 流式回复 (stream) 和模板卡片回复
    - 事件接收 (enter_chat/template_card_event/feedback_event)
    - 主动消息推送
    - 文件下载 + AES-256-CBC 解密
    """

    channel_name = "wework_ws"

    def __init__(
        self,
        bot_id: str,
        secret: str,
        ws_url: str = WS_DEFAULT_URL,
        media_dir: Path | None = None,
        *,
        channel_name: str | None = None,
        bot_id_alias: str | None = None,
        agent_profile_id: str = "default",
        webhook_url: str = "",
    ):
        super().__init__(
            channel_name=channel_name,
            bot_id=bot_id_alias,
            agent_profile_id=agent_profile_id,
        )

        self.config = WeWorkWsConfig(bot_id=bot_id, secret=secret, ws_url=ws_url)
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/wework_ws")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        # Webhook 辅助发送器（用于发送图片/语音/文件）
        self._webhook: _WebhookSender | None = (
            _WebhookSender(webhook_url) if webhook_url else None
        )

        # WebSocket state
        self._ws: Any = None
        self._connection_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._authenticated = asyncio.Event()
        self._missed_pong = 0

        # reply ack
        self._pending_acks: dict[str, asyncio.Future] = {}
        self._reply_locks: dict[str, asyncio.Lock] = {}

        # message dedup
        self._seen_msg_ids: OrderedDict[str, None] = OrderedDict()
        self._seen_msg_ids_max = 500

        # response_url cache: req_id → url
        self._response_urls: dict[str, str] = {}

        # thinking indicator: pre-created stream_id per req_id
        self._pre_streams: dict[str, str] = {}

        # queued image items: send_image queues here, _send_stream_reply drains
        self._pending_image_items: dict[str, list[dict]] = {}

        # background tasks ref holder
        self._bg_tasks: set[asyncio.Task] = set()

    # ==================== Properties ====================

    @property
    def supports_streaming(self) -> bool:
        return True

    # ==================== Lifecycle ====================

    async def start(self) -> None:
        _import_websockets()
        self._running = True
        self._connection_task = asyncio.create_task(self._connection_loop())
        logger.info(
            f"WeWork WS adapter starting, will connect to {self.config.ws_url}"
        )

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
        if self._connection_task:
            self._connection_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._connection_task
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._webhook:
            await self._webhook.close()
        self._reject_all_pending("adapter stopped")
        logger.info("WeWork WS adapter stopped")

    # ==================== Connection loop ====================

    async def _connection_loop(self) -> None:
        """Main connection loop with exponential back-off reconnect."""
        attempt = 0
        while self._running:
            try:
                await self._connect_and_run()
                attempt = 0
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"WeWork WS connection error: {e}")

            if not self._running:
                return

            # check max reconnect
            max_att = self.config.max_reconnect_attempts
            if max_att != -1 and attempt >= max_att:
                logger.error(
                    f"Max reconnect attempts ({max_att}) reached, giving up"
                )
                return

            attempt += 1
            delay = min(
                self.config.reconnect_base_delay * (2 ** (attempt - 1)),
                self.config.reconnect_max_delay,
            )
            logger.info(f"Reconnecting in {delay:.1f}s (attempt {attempt})...")
            await asyncio.sleep(delay)

    async def _connect_and_run(self) -> None:
        """Single connection lifetime: connect → auth → heartbeat + receive."""
        self._authenticated.clear()
        self._missed_pong = 0
        self._reject_all_pending("reconnecting")

        async with websockets.connect(
            self.config.ws_url,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            logger.info(f"WebSocket connected to {self.config.ws_url}")

            receive_task = asyncio.create_task(self._receive_loop(ws))

            await self._send_auth()
            try:
                await asyncio.wait_for(
                    self._authenticated.wait(), timeout=10.0
                )
            except asyncio.TimeoutError:
                logger.error("Authentication timeout (10s)")
                receive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await receive_task
                return

            logger.info("WebSocket authenticated successfully")

            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            try:
                await receive_task
            finally:
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._heartbeat_task
                self._ws = None

    # ==================== Auth ====================

    async def _send_auth(self) -> None:
        frame = {
            "cmd": CMD_SUBSCRIBE,
            "headers": {"req_id": _generate_req_id(CMD_SUBSCRIBE)},
            "body": {
                "bot_id": self.config.bot_id,
                "secret": self.config.secret,
            },
        }
        await self._ws_send(frame)
        logger.debug("Auth frame sent")

    # ==================== Heartbeat ====================

    async def _heartbeat_loop(self) -> None:
        """Send heartbeat every interval; kill connection on too many missed pongs."""
        try:
            while self._running and self._ws:
                await asyncio.sleep(self.config.heartbeat_interval)

                if self._missed_pong >= self.config.max_missed_pong:
                    logger.warning(
                        f"No heartbeat ack for {self._missed_pong} pings, "
                        "connection considered dead"
                    )
                    if self._ws:
                        await self._ws.close()
                    return

                self._missed_pong += 1
                frame = {
                    "cmd": CMD_HEARTBEAT,
                    "headers": {"req_id": _generate_req_id(CMD_HEARTBEAT)},
                }
                try:
                    await self._ws_send(frame)
                except Exception as e:
                    logger.error(f"Failed to send heartbeat: {e}")
                    return
        except asyncio.CancelledError:
            return

    # ==================== Receive loop ====================

    async def _receive_loop(self, ws) -> None:
        """Read frames and route them."""
        try:
            async for raw in ws:
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from WS: {raw!r:.200}")
                    continue
                try:
                    await self._route_frame(frame)
                except Exception as e:
                    logger.error(f"Error routing frame: {e}", exc_info=True)
        except websockets.ConnectionClosed as e:
            logger.warning(f"WebSocket closed: {e}")
        except asyncio.CancelledError:
            raise

    # ==================== Frame router ====================

    async def _route_frame(self, frame: dict) -> None:
        cmd = frame.get("cmd")
        req_id: str = frame.get("headers", {}).get("req_id", "")

        # 1. Message callback
        if cmd == CMD_CALLBACK:
            task = asyncio.create_task(self._handle_msg_callback(frame))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
            return

        # 2. Event callback
        if cmd == CMD_EVENT_CALLBACK:
            task = asyncio.create_task(self._handle_event_callback(frame))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
            return

        # 3. No cmd → ack / auth response / heartbeat response
        if cmd is None or cmd == "":
            # reply ack
            if req_id in self._pending_acks:
                fut = self._pending_acks.pop(req_id)
                if not fut.done():
                    fut.set_result(frame)
                return

            errcode = frame.get("errcode")

            # auth response
            if req_id.startswith(CMD_SUBSCRIBE):
                if errcode == 0:
                    self._authenticated.set()
                else:
                    errmsg = frame.get("errmsg", "unknown")
                    logger.error(f"Auth failed: {errcode} {errmsg}")
                return

            # heartbeat response
            if req_id.startswith(CMD_HEARTBEAT):
                if errcode == 0:
                    self._missed_pong = 0
                return

        logger.debug(f"Unhandled frame cmd={cmd} req_id={req_id}")

    # ==================== Message handling ====================

    async def _handle_msg_callback(self, frame: dict) -> None:
        body: dict = frame.get("body", {})
        req_id: str = frame.get("headers", {}).get("req_id", "")
        msgid = body.get("msgid", "")

        # dedup
        if msgid in self._seen_msg_ids:
            logger.debug(f"Duplicate msgid={msgid}, skipping")
            return
        self._seen_msg_ids[msgid] = None
        if len(self._seen_msg_ids) > self._seen_msg_ids_max:
            self._seen_msg_ids.popitem(last=False)

        # cache response_url
        response_url = body.get("response_url")
        if response_url and req_id:
            self._response_urls[req_id] = response_url
            self._cleanup_response_urls()

        msgtype = body.get("msgtype", "")
        chattype = body.get("chattype", "single")
        chat_type = "group" if chattype == "group" else "private"
        from_user = body.get("from", {}).get("userid", "unknown")
        chat_id = body.get("chatid", from_user)

        # parse content
        content, media_list = self._parse_content(body, msgtype)

        # is_mentioned: in WS mode, messages are only delivered when bot is
        # actually addressed (unlike HTTP callback which receives all group msgs).
        # For single chat it's always True; for group we default True as well
        # because the platform already filters.
        is_mentioned = True
        is_direct = chat_type == "private"

        unified = UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=msgid,
            user_id=f"wework_{from_user}",
            channel_user_id=from_user,
            chat_id=chat_id,
            content=content,
            chat_type=chat_type,
            is_mentioned=is_mentioned,
            is_direct_message=is_direct,
            raw=body,
            metadata={"req_id": req_id},
        )

        self._log_message(unified)

        # thinking indicator MUST be sent before _emit_message to avoid a race:
        # if gateway processes the message and calls send_message before
        # _pre_streams is populated, a duplicate stream would be created.
        await self._maybe_send_thinking_indicator(req_id)

        await self._emit_message(unified)

    def _parse_content(
        self, body: dict, msgtype: str
    ) -> tuple[MessageContent, list[MediaFile]]:
        """Parse message body into MessageContent + media list."""
        media_list: list[MediaFile] = []

        if msgtype == "text":
            text_data = body.get("text", {})
            return MessageContent(text=text_data.get("content", "")), media_list

        if msgtype == "image":
            img = body.get("image", {})
            media = MediaFile.create(
                filename="image.jpg",
                mime_type="image/jpeg",
                url=img.get("url"),
            )
            media.extra = {"aeskey": img.get("aeskey")}
            media_list.append(media)
            return MessageContent(images=[media]), media_list

        if msgtype == "mixed":
            mixed_data = body.get("mixed", {})
            items = mixed_data.get("msg_item", [])
            text_parts: list[str] = []
            images: list[MediaFile] = []
            for item in items:
                item_type = item.get("msgtype", "")
                if item_type == "text":
                    text_parts.append(item.get("text", {}).get("content", ""))
                elif item_type == "image":
                    img_data = item.get("image", {})
                    media = MediaFile.create(
                        filename=f"image_{len(images)}.jpg",
                        mime_type="image/jpeg",
                        url=img_data.get("url"),
                    )
                    media.extra = {"aeskey": img_data.get("aeskey")}
                    images.append(media)
                    media_list.append(media)
            return (
                MessageContent(text="\n".join(text_parts) or None, images=images),
                media_list,
            )

        if msgtype == "voice":
            voice_data = body.get("voice", {})
            platform_text = voice_data.get("content", "").strip()
            if platform_text:
                return (MessageContent(text=platform_text), media_list)
            logger.warning(
                "[WeWorkWS] Voice transcription empty, msgid=%s",
                body.get("msgid"),
            )
            return (
                MessageContent(
                    text="[语音消息，平台未能识别，请重新发送或改用文字]"
                ),
                media_list,
            )

        if msgtype == "file":
            file_data = body.get("file", {})
            media = MediaFile.create(
                filename=file_data.get("filename", "file"),
                mime_type="application/octet-stream",
                url=file_data.get("url"),
            )
            media.extra = {"aeskey": file_data.get("aeskey")}
            media_list.append(media)
            return MessageContent(files=[media]), media_list

        logger.debug(f"Unhandled msgtype: {msgtype}")
        return MessageContent(text=f"[不支持的消息类型: {msgtype}]"), media_list

    # ==================== Event handling ====================

    async def _handle_event_callback(self, frame: dict) -> None:
        body: dict = frame.get("body", {})
        req_id: str = frame.get("headers", {}).get("req_id", "")
        event_data = body.get("event", {})
        event_type = event_data.get("eventtype", "")

        logger.info(f"Event received: {event_type}")

        if event_type == "enter_chat":
            await self._emit_event("enter_chat", {
                "req_id": req_id,
                "chatid": body.get("chatid", ""),
                "chattype": body.get("chattype", ""),
                "userid": body.get("from", {}).get("userid", ""),
                "aibotid": body.get("aibotid", ""),
            })
        elif event_type == "template_card_event":
            await self._emit_event("template_card_event", {
                "req_id": req_id,
                "event_key": event_data.get("event_key", ""),
                "task_id": event_data.get("task_id", ""),
                "chatid": body.get("chatid", ""),
                "userid": body.get("from", {}).get("userid", ""),
            })
        elif event_type == "feedback_event":
            await self._emit_event("feedback_event", {
                "req_id": req_id,
                "chatid": body.get("chatid", ""),
                "userid": body.get("from", {}).get("userid", ""),
                "raw": body,
            })
        else:
            logger.debug(f"Unhandled event type: {event_type}")

    # ==================== Thinking indicator ====================

    async def _maybe_send_thinking_indicator(self, req_id: str) -> None:
        """Pre-send a 'thinking' stream frame so the user sees immediate feedback."""
        from openakita.config import settings

        if not getattr(settings, "wework_ws_thinking_indicator", True):
            return
        if not req_id or not self._ws:
            return

        stream_id = secrets.token_hex(16)
        body: dict = {
            "msgtype": "stream",
            "stream": {
                "id": stream_id,
                "finish": False,
                "content": "思考中...",
            },
        }
        try:
            await self._send_reply_with_ack(req_id, body, CMD_RESPONSE)
            self._pre_streams[req_id] = stream_id
        except Exception as e:
            logger.debug(f"Thinking indicator send failed (non-fatal): {e}")

    # ==================== Sending ====================

    async def send_message(self, message: OutgoingMessage) -> str:
        """Send a message (reply via stream or active push via markdown)."""
        text = message.content.text or ""
        chat_id = message.chat_id
        req_id = message.metadata.get("req_id", "")

        # If we have a req_id, this is a reply to an incoming message
        if req_id:
            return await self._send_stream_reply(req_id, text, message)

        # Otherwise, active push
        return await self._send_active_message(chat_id, text)

    async def send_image(
        self,
        chat_id: str,
        image_path: str,
        caption: str | None = None,
        reply_to: str | None = None,
        **kwargs,
    ) -> str:
        """Send image: prefer webhook, fallback to msg_item or markdown hint."""
        # 1. Webhook 通道（最可靠）
        if self._webhook:
            ok = await self._webhook.send_image(image_path)
            if ok:
                logger.info(f"[send_image] Sent via webhook: {Path(image_path).name}")
                return "webhook:image_sent"

        # 2. msg_item 内联（stream reply 中）
        from openakita.config import settings

        req_id = (kwargs.get("metadata") or {}).get("req_id", "")
        use_msg_item = getattr(settings, "wework_ws_msg_item_images", True)
        if req_id and use_msg_item:
            path = Path(image_path)
            if not path.exists():
                logger.warning(f"[send_image] Image file not found: {path}")
                return ""
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, self._compress_image, path)
            b64 = base64.b64encode(data).decode("ascii")
            md5 = hashlib.md5(data).hexdigest()
            item = {"msgtype": "image", "image": {"base64": b64, "md5": md5}}
            self._pending_image_items.setdefault(req_id, []).append(item)
            logger.info(
                f"[send_image] Queued image for stream reply: "
                f"req_id={req_id}, file={path.name}, "
                f"compressed={len(data)}, b64={len(b64)}"
            )
            return f"queued:{req_id}"

        # 3. Markdown hint fallback
        path = Path(image_path)
        label = caption or path.name
        if req_id:
            logger.info(
                f"[send_image] msg_item disabled, skipping image in reply: {path.name}"
            )
            return f"skipped:{req_id}"
        desc = f"> 📎 **{label}**\n> （企业微信长连接暂不支持发送图片）"
        logger.debug(f"WS long-connection does not support image, sending hint: {path.name}")
        return await self._send_active_message(chat_id, desc)

    @staticmethod
    def _compress_image(path: Path) -> bytes:
        """Compress image for msg_item: resize + JPEG conversion if oversized."""
        raw = path.read_bytes()
        if len(raw) <= MSG_ITEM_IMAGE_MAX_BYTES:
            return raw
        try:
            import io
            from PIL import Image

            img = Image.open(path)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            if img.width > MSG_ITEM_IMAGE_MAX_WIDTH:
                ratio = MSG_ITEM_IMAGE_MAX_WIDTH / img.width
                img = img.resize(
                    (MSG_ITEM_IMAGE_MAX_WIDTH, int(img.height * ratio)),
                    Image.LANCZOS,
                )
            quality = 85
            for _ in range(5):
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                data = buf.getvalue()
                if len(data) <= MSG_ITEM_IMAGE_MAX_BYTES:
                    logger.info(
                        f"[compress_image] {path.name}: "
                        f"{len(raw)} -> {len(data)} bytes (q={quality})"
                    )
                    return data
                quality -= 10
            logger.info(
                f"[compress_image] {path.name}: "
                f"{len(raw)} -> {len(data)} bytes (best effort, q={quality + 10})"
            )
            return data
        except Exception as e:
            logger.warning(f"[compress_image] Failed to compress {path.name}: {e}")
            return raw

    async def send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
        **kwargs,
    ) -> str:
        """Send a file: prefer webhook, fallback to markdown hint."""
        # Webhook 通道
        if self._webhook:
            ok = await self._webhook.send_file(file_path)
            if ok:
                logger.info(f"[send_file] Sent via webhook: {Path(file_path).name}")
                return "webhook:file_sent"

        req_id = (kwargs.get("metadata") or {}).get("req_id", "")
        if req_id:
            logger.info(
                f"[send_file] File delivery in reply context (msg_item unsupported), "
                f"skipped: {Path(file_path).name}"
            )
            return f"skipped:{req_id}"
        path = Path(file_path)
        try:
            size_bytes = path.stat().st_size
            if size_bytes >= 1024 * 1024:
                size_str = f"{size_bytes / (1024 * 1024):.1f}MB"
            else:
                size_str = f"{size_bytes / 1024:.0f}KB"
        except OSError:
            size_str = ""
        label = caption or path.name
        size_part = f" ({size_str})" if size_str else ""
        desc = f"> 📎 **{label}**{size_part}\n> （企业微信长连接暂不支持发送文件）"
        logger.debug(f"WS long-connection does not support file, sending hint: {path.name}")
        return await self._send_active_message(chat_id, desc)

    async def send_voice(
        self,
        chat_id: str,
        voice_path: str,
        caption: str | None = None,
        reply_to: str | None = None,
        **kwargs,
    ) -> str:
        """Send voice: only available via webhook."""
        if self._webhook:
            ok = await self._webhook.send_voice(voice_path)
            if ok:
                logger.info(f"[send_voice] Sent via webhook: {Path(voice_path).name}")
                return "webhook:voice_sent"
            logger.warning(f"[send_voice] Webhook send_voice failed: {Path(voice_path).name}")
        raise NotImplementedError(
            "WeWork WS adapter requires wework_ws_webhook_url to send voice messages"
        )

    async def _send_stream_reply(
        self, req_id: str, text: str, message: OutgoingMessage
    ) -> str:
        """Send a stream reply for an incoming message."""
        pre_stream_id = self._pre_streams.pop(req_id, None)
        stream_id = pre_stream_id or secrets.token_hex(16)
        encoded = text.encode("utf-8")

        # collect images: from OutgoingMessage + queued by send_image
        img_items = await self._prepare_image_items(message)
        queued = self._pending_image_items.pop(req_id, [])
        if queued:
            img_items.extend(queued)
            logger.info(f"[stream_reply] Attached {len(queued)} queued image(s) to req_id={req_id}")

        # Stream split: if we have images AND reused a thinking-indicator stream,
        # close the old stream first and start a fresh one for the image reply.
        # The WeChat Work server may not render msg_item on a stream that already
        # had a finish=false "thinking" frame.
        if img_items and pre_stream_id:
            logger.info(
                f"[stream_reply] Closing thinking-indicator stream "
                f"{pre_stream_id} before sending image reply"
            )
            close_body: dict = {
                "msgtype": "stream",
                "stream": {"id": pre_stream_id, "finish": True, "content": ""},
            }
            try:
                await self._send_reply_with_ack(req_id, close_body, CMD_RESPONSE)
            except Exception as e:
                logger.warning(f"Failed to close thinking stream: {e}")
            stream_id = secrets.token_hex(16)

        # split into chunks if content exceeds max
        chunks = []
        offset = 0
        while offset < len(encoded):
            chunk = encoded[offset : offset + STREAM_CONTENT_MAX_BYTES]
            # avoid splitting in the middle of a multi-byte UTF-8 char
            try:
                chunk.decode("utf-8")
            except UnicodeDecodeError:
                chunk = chunk[:-1]
                while chunk and chunk[-1] & 0xC0 == 0x80:
                    chunk = chunk[:-1]
            if not chunk:
                break
            chunks.append(chunk.decode("utf-8", errors="ignore"))
            offset += len(chunk)

        if not chunks:
            chunks = [""]

        for i, chunk_text in enumerate(chunks):
            is_last = i == len(chunks) - 1
            body: dict = {
                "msgtype": "stream",
                "stream": {
                    "id": stream_id,
                    "finish": is_last,
                    "content": chunk_text,
                },
            }
            if is_last and img_items:
                body["stream"]["msg_item"] = img_items
                logger.info(
                    f"[stream_reply] Final frame: stream_id={stream_id}, "
                    f"finish={is_last}, content_len={len(chunk_text)}, "
                    f"img_count={len(img_items)}, "
                    f"img0_b64_len={len(img_items[0]['image']['base64'])}"
                )

            try:
                await self._send_reply_with_ack(req_id, body, CMD_RESPONSE)
            except Exception as e:
                logger.error(f"Stream reply failed at chunk {i}: {e}")
                # only fallback when NO chunks have been sent yet;
                # partial stream already visible to user, fallback would duplicate
                if i == 0:
                    await self._response_url_fallback(req_id, text)
                return ""

        # cleanup lock after all chunks sent
        self._reply_locks.pop(req_id, None)
        return stream_id

    async def _send_active_message(self, chat_id: str, text: str) -> str:
        """Send an active push message (markdown)."""
        req_id = _generate_req_id(CMD_SEND_MSG)
        body: dict = {
            "chatid": chat_id,
            "msgtype": "markdown",
            "markdown": {"content": text},
        }
        try:
            await self._send_reply_with_ack(req_id, body, CMD_SEND_MSG)
        except Exception as e:
            logger.error(f"Active message send failed: {e}")
        return req_id

    async def _prepare_image_items(self, message: OutgoingMessage) -> list[dict]:
        """Prepare base64 image items from OutgoingMessage (max 10)."""
        items: list[dict] = []
        images = message.content.images[:10]
        loop = asyncio.get_running_loop()
        for media in images:
            if not media.local_path:
                continue
            try:
                path = Path(media.local_path)
                data = await loop.run_in_executor(None, path.read_bytes)
                b64 = base64.b64encode(data).decode("ascii")
                md5 = hashlib.md5(data).hexdigest()
                items.append({
                    "msgtype": "image",
                    "image": {"base64": b64, "md5": md5},
                })
            except Exception as e:
                logger.warning(f"Failed to prepare image {media.local_path}: {e}")
        return items

    # ==================== Reply with ack ====================

    async def _send_reply_with_ack(
        self, req_id: str, body: dict, cmd: str
    ) -> dict:
        """Send a reply frame and wait for ack, with per-req_id serial ordering."""
        if not self._ws:
            raise ConnectionError("WebSocket not connected")

        # get or create per-req_id lock for serial sending
        if req_id not in self._reply_locks:
            self._reply_locks[req_id] = asyncio.Lock()
        lock = self._reply_locks[req_id]

        async with lock:
            frame = {
                "cmd": cmd,
                "headers": {"req_id": req_id},
                "body": body,
            }

            # register ack future before sending
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending_acks[req_id] = fut

            try:
                await self._ws_send(frame)
            except Exception:
                self._pending_acks.pop(req_id, None)
                raise

            try:
                result = await asyncio.wait_for(
                    fut, timeout=self.config.reply_ack_timeout
                )
            except asyncio.TimeoutError:
                self._pending_acks.pop(req_id, None)
                raise TimeoutError(
                    f"Reply ack timeout ({self.config.reply_ack_timeout}s) "
                    f"for req_id={req_id}"
                )

            errcode = result.get("errcode")
            if errcode is not None and errcode != 0:
                errmsg = result.get("errmsg", "unknown")
                raise RuntimeError(f"Reply rejected: {errcode} {errmsg}")

            return result

    # ==================== response_url fallback ====================

    async def _response_url_fallback(self, req_id: str, text: str) -> bool:
        """Try to send via response_url when WS reply fails."""
        url = self._response_urls.get(req_id)
        if not url:
            logger.debug(f"No response_url for req_id={req_id}, cannot fallback")
            return False

        _import_httpx()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                payload = {
                    "msgtype": "markdown",
                    "markdown": {"content": text},
                }
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    logger.info(f"response_url fallback succeeded for {req_id}")
                    return True
                logger.warning(
                    f"response_url fallback status={resp.status_code} for {req_id}"
                )
        except Exception as e:
            logger.error(f"response_url fallback failed: {e}")
        return False

    # ==================== Media ====================

    async def download_media(self, media: MediaFile) -> Path:
        """Download and optionally decrypt media file."""
        if not media.url:
            raise ValueError("Media has no URL")

        _import_httpx()

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=30.0)
        ) as client:
            resp = await client.get(media.url)
            resp.raise_for_status()

            data = resp.content

            # parse filename from Content-Disposition
            cd = resp.headers.get("content-disposition", "")
            filename = media.filename
            if cd:
                m = re.search(r"filename\*=UTF-8''([^;\s]+)", cd, re.IGNORECASE)
                if m:
                    from urllib.parse import unquote
                    filename = unquote(m.group(1))
                else:
                    m = re.search(r'filename="?([^";\s]+)"?', cd, re.IGNORECASE)
                    if m:
                        from urllib.parse import unquote
                        filename = unquote(m.group(1))

            # decrypt if aeskey provided
            aeskey = (media.extra or {}).get("aeskey")
            if aeskey:
                loop = asyncio.get_running_loop()
                data = await loop.run_in_executor(
                    None, _decrypt_file, data, aeskey
                )

            local_path = self.media_dir / f"{media.id}_{filename}"
            await asyncio.get_running_loop().run_in_executor(
                None, local_path.write_bytes, data
            )

            media.local_path = str(local_path)
            media.status = MediaStatus.READY
            media.filename = filename
            logger.info(f"Media downloaded: {local_path}")
            return local_path

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """Upload not supported in WS mode — files are sent inline as base64."""
        raise NotImplementedError(
            "WeWork WS adapter does not support upload_media; "
            "images are sent inline as base64 in stream msg_item"
        )

    # ==================== Helpers ====================

    async def _ws_send(self, frame: dict) -> None:
        """Send a JSON frame over WebSocket."""
        if self._ws is None:
            raise ConnectionError("WebSocket not connected")
        await self._ws.send(json.dumps(frame, ensure_ascii=False))

    def _reject_all_pending(self, reason: str) -> None:
        """Reject all pending ack futures and clear connection-scoped state."""
        for req_id, fut in list(self._pending_acks.items()):
            if not fut.done():
                fut.set_exception(ConnectionError(reason))
        self._pending_acks.clear()
        self._reply_locks.clear()
        self._pre_streams.clear()
        self._pending_image_items.clear()

    # cleanup response_url cache periodically (keep last 200)
    def _cleanup_response_urls(self) -> None:
        if len(self._response_urls) > 200:
            keys = list(self._response_urls.keys())
            for k in keys[: len(keys) - 200]:
                del self._response_urls[k]
