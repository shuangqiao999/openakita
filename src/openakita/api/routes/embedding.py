"""
嵌入模型配置 API 端点

提供嵌入模型的读取、保存、测试、模型列表拉取功能。
所有端点返回的 api_key 均脱敏处理。
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openakita.llm.embeddings import clear_embedding_cache, test_embedding_model

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/embedding", tags=["嵌入模型"])


class EmbeddingConfigBody(BaseModel):
    provider: str = ""
    model_name: str = ""
    api_base: str = ""
    api_key: str = ""
    device: str = "cpu"


class FetchModelsBody(BaseModel):
    provider: str = ""
    api_base: str = ""
    api_key: str = ""


def _mask_key(key: str) -> str:
    if not key or len(key) <= 8:
        return key[:3] + "***" if len(key) >= 3 else "***"
    return key[:4] + "****" + key[-4:]


def _read_embedding_config() -> dict:
    try:
        from openakita.config import settings

        raw_key = getattr(settings, "embedding_api_key", "") or ""
        return {
            "provider": getattr(settings, "embedding_provider", "") or "",
            "model_name": getattr(settings, "embedding_model_name", "") or "",
            "api_base": getattr(settings, "embedding_api_base", "") or "",
            "api_key": _mask_key(raw_key) if raw_key else "",
            "api_key_configured": bool(raw_key),
            "device": getattr(settings, "embedding_device", "cpu") or "cpu",
        }
    except Exception:
        return {}


def _save_embedding_config(config: EmbeddingConfigBody) -> None:
    dotenv_path = None
    try:
        from openakita.config import settings

        dotenv_path = getattr(settings, "dotenv_path", None)
        if not dotenv_path:
            dotenv_path = getattr(settings, "env_file", None) or ".env"
    except Exception:
        dotenv_path = ".env"

    dotenv_path = str(dotenv_path)

    env_updates: dict[str, str] = {
        "EMBEDDING_PROVIDER": config.provider,
        "EMBEDDING_MODEL_NAME": config.model_name,
        "EMBEDDING_API_BASE": config.api_base,
        "EMBEDDING_DEVICE": config.device,
    }
    # 支持清除 API key: 前端传空字符串时也写入空值
    env_updates["EMBEDDING_API_KEY"] = config.api_key

    # 原子写入 .env: 先写临时文件, 再 os.replace (原子操作)
    if dotenv_path:
        env_path = Path(dotenv_path)
        if env_path.exists():
            try:
                lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
            except Exception:
                logger.exception(f"[EmbeddingAPI] Failed to read {dotenv_path}, aborting")
                return
            existing_keys: set[str] = set()
            new_lines: list[str] = []
            for line in lines:
                matched = False
                for key in env_updates:
                    if line.startswith(f"{key}="):
                        new_lines.append(f"{key}={env_updates[key]}\n")
                        existing_keys.add(key)
                        matched = True
                        break
                if not matched:
                    new_lines.append(line.rstrip("\n") + "\n")
            for key, val in env_updates.items():
                if key not in existing_keys:
                    new_lines.append(f"{key}={val}\n")

            content = "".join(new_lines)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(env_path.parent), prefix=".env.", suffix=".tmp"
            )
            try:
                os.write(fd, content.encode("utf-8"))
            finally:
                os.close(fd)
            os.replace(tmp_path, str(env_path))
        else:
            content = "\n".join(f"{k}={v}" for k, v in env_updates.items()) + "\n"
            env_path.parent.mkdir(parents=True, exist_ok=True)
            env_path.write_text(content, encoding="utf-8")

    # 热重载 Settings (通过 setattr 更新常驻字段)
    try:
        from openakita.config import settings as _cfg

        for key, val in env_updates.items():
            setattr(_cfg, key.lower(), val)
    except Exception as e:
        logger.warning(f"[EmbeddingAPI] Failed to hot-reload settings: {e}")

    clear_embedding_cache()


@router.get("/config")
async def get_embedding_config():
    return _read_embedding_config()


@router.post("/config")
async def save_embedding_config(body: EmbeddingConfigBody):
    try:
        _save_embedding_config(body)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"保存嵌入模型配置失败: {e}")


@router.post("/test")
async def test_embedding_endpoint(body: EmbeddingConfigBody):
    config = {
        "provider": body.provider,
        "model_name": body.model_name,
        "api_base": body.api_base,
        "api_key": body.api_key,
        "device": body.device,
    }
    try:
        result = await test_embedding_model(config)
        return result
    except Exception:
        return {"success": False, "latency_ms": 0, "dimension": 0, "error": "内部错误"}


@router.post("/models")
async def list_embedding_models(body: FetchModelsBody):
    """拉取嵌入模型列表 (仅 OpenAI 兼容 API 支持)。API key 通过 POST body 传递避免 URL 泄露。"""
    if not body.api_base and not body.provider:
        return {"models": []}
    if body.provider in ("openai", "") and body.api_key:
        import httpx

        base = (body.api_base or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/models"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                headers = {"Authorization": f"Bearer {body.api_key}"}
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                all_models = [m.get("id", "") for m in data.get("data", [])]
                embedding_models = [
                    m for m in all_models if "embed" in m.lower() or "bge" in m.lower()
                ]
                return {"models": embedding_models or all_models[:50]}
        except Exception:
            return {"models": [], "error": "拉取模型列表失败"}
    return {"models": []}
