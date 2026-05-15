"""
Desktop attachment processing helpers.

Extracted from agent.py to keep the Agent class focused on orchestration.
Handles:
- Local image inlining (base64 data URIs for cloud LLM compatibility)
- Data URI persistence (save base64 payloads as files)
- Desktop attachment reference formatting for prompt injection
"""

import base64
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Compiled regexes ──
_LOCAL_UPLOAD_RE = re.compile(
    r"^(?:https?://(?:127\.0\.0\.1|localhost|0\.0\.0\.0)(?::\d+)?)?/api/uploads/([\w\-.]+)$",
    re.IGNORECASE,
)
_DATA_URI_RE = re.compile(
    r"^data:(?P<mime>[^;,]+)?(?P<params>(?:;[^,]*)*),(?P<data>.*)$",
    re.DOTALL,
)

_INLINE_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


def maybe_inline_local_image(att_url: str, att_mime: str) -> str | None:
    """If *att_url* points to a locally served upload, return a base64 data URL.

    Returns None when the URL is not local, file is missing/too large, or
    any IO error occurs — caller falls back to its existing degraded path.
    """
    if not att_url or att_url.startswith("data:"):
        return None
    m = _LOCAL_UPLOAD_RE.match(att_url.strip())
    if not m:
        return None
    filename = m.group(1)
    try:
        from ..api.routes.upload import get_upload_dir

        upload_dir = get_upload_dir().resolve()
        filepath = (upload_dir / filename).resolve()
        filepath.relative_to(upload_dir)
        if not filepath.is_file():
            return None
        size = filepath.stat().st_size
        if size > _INLINE_IMAGE_MAX_BYTES:
            logger.info(
                "[InlineImage] skip %s: %.1f MB exceeds limit",
                filename, size / 1024 / 1024,
            )
            return None
        mime = att_mime or ""
        if not mime.startswith("image/"):
            import mimetypes

            mime = mimetypes.guess_type(str(filepath))[0] or "image/png"
        data = filepath.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception as exc:
        logger.warning("[InlineImage] failed to inline %s: %s", att_url, exc)
        return None


def safe_attachment_stem(filename: str) -> str:
    """Sanitize filename to a safe stem for disk storage."""
    stem = Path(filename or "attachment").stem or "attachment"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    return stem[:80] or "attachment"


def save_data_uri_attachment(
    att_url: str,
    *,
    att_name: str,
    att_mime: str,
) -> dict[str, Any] | None:
    """Persist non-media data URI attachments and return a short local reference.

    Desktop/API clients should normally upload files through /api/upload. This
    fallback prevents old clients from replaying large base64 payloads into the
    LLM prompt while still preserving the file for tools to inspect.
    """
    match = _DATA_URI_RE.match((att_url or "").strip())
    if not match:
        return None

    try:
        import mimetypes
        from urllib.parse import unquote_to_bytes

        from ..api.routes.upload import (
            BLOCKED_EXTENSIONS,
            MAX_UPLOAD_SIZE,
            get_upload_dir,
        )

        mime = (att_mime or match.group("mime") or "application/octet-stream").strip()
        payload = match.group("data") or ""
        params = (match.group("params") or "").lower()
        if ";base64" in params:
            raw = base64.b64decode(payload, validate=False)
        else:
            raw = unquote_to_bytes(payload)

        if len(raw) > MAX_UPLOAD_SIZE:
            logger.warning(
                "[DesktopAttachment] data URI attachment %s exceeds upload limit: %.1f MB",
                att_name,
                len(raw) / 1024 / 1024,
            )
            return None

        original = Path(att_name or "attachment")
        suffix = original.suffix.lower()
        if suffix in BLOCKED_EXTENSIONS:
            suffix = ".bin"
        if not suffix:
            suffix = mimetypes.guess_extension(mime) or ".bin"

        filename = (
            f"{int(time.time())}_{uuid.uuid4().hex[:8]}_"
            f"{safe_attachment_stem(att_name)}{suffix}"
        )
        filepath = get_upload_dir() / filename
        filepath.write_bytes(raw)
        return {
            "url": f"/api/uploads/{filename}",
            "local_path": str(filepath),
            "mime_type": mime,
            "size": len(raw),
        }
    except Exception as exc:
        logger.warning(
            "[DesktopAttachment] failed to persist data URI attachment %s: %s",
            att_name,
            exc,
        )
        return None


def format_desktop_attachment_reference(
    *,
    att_type: str,
    att_name: str,
    att_mime: str,
    att_url: str,
    att_local_path: str | None = None,
    att_size: int | None = None,
) -> str:
    """Return a prompt-safe text reference for non-image/video attachments."""
    if (att_url or "").strip().startswith("data:"):
        saved = save_data_uri_attachment(att_url, att_name=att_name, att_mime=att_mime)
        if saved:
            return (
                f"[附件: {att_name} ({saved['mime_type']})，"
                f"已保存到本地路径: {saved['local_path']}，"
                f"URL: {saved['url']}，大小: {saved['size']} bytes。"
                "如需读取内容，请使用文件或数据处理工具打开该本地路径。]"
            )
        return (
            f"[附件: {att_name} ({att_mime or att_type}) 是内联 data URI，"
            "为避免超大 base64 内容进入模型上下文，已隐藏原始内容。"
            "请使用上传文件 URL 或重新上传附件后继续处理。]"
        )

    local_path = att_local_path
    if not local_path and att_url:
        try:
            from ..api.routes.upload import resolve_upload_path

            resolved = resolve_upload_path(att_url)
            if resolved:
                local_path = str(resolved)
                att_size = resolved.stat().st_size
        except Exception as exc:
            logger.debug("[DesktopAttachment] failed to resolve upload path %s: %s", att_url, exc)

    if att_type == "document":
        label = "文档"
    elif att_type == "voice" or (att_mime or "").startswith("audio/"):
        label = "音频"
    else:
        label = "附件"

    size_text = f"，大小: {att_size} bytes" if att_size is not None else ""
    if local_path:
        return (
            f"[{label}: {att_name} ({att_mime or att_type})，"
            f"已保存到本地路径: {local_path}，URL: {att_url or '无'}{size_text}。"
            "如需读取、转写或分析，请直接使用文件/音频处理工具打开该本地路径。]"
        )
    return f"[{label}: {att_name} ({att_mime or att_type})] URL: {att_url}"
