"""
嵌入模型配置 API 端点

提供嵌入模型的读取、保存、测试功能。
Rust Tauri 端通过 HTTP 调用这些端点来管理嵌入模型配置。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openakita.llm.embeddings import (
    clear_embedding_cache,
    test_embedding_model,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/embedding", tags=["嵌入模型"])


class EmbeddingConfigBody(BaseModel):
    provider: str = ""  # openai | huggingface | custom | ""
    model_name: str = ""
    api_base: str = ""
    api_key: str = ""
    device: str = "cpu"  # cpu | cuda


def _read_embedding_config() -> dict:
    try:
        from openakita.config import settings

        return {
            "provider": getattr(settings, "embedding_provider", "") or "",
            "model_name": getattr(settings, "embedding_model_name", "") or "",
            "api_base": getattr(settings, "embedding_api_base", "") or "",
            "api_key": getattr(settings, "embedding_api_key", "") or "",
            "device": getattr(settings, "embedding_device", "cpu") or "cpu",
        }
    except Exception:
        return {}


def _save_embedding_config(config: EmbeddingConfigBody) -> None:
    import os

    dotenv_path = None
    try:
        from openakita.config import settings

        dotenv_path = getattr(settings, "dotenv_path", None) or ".env"
    except Exception:
        dotenv_path = ".env"

    env_updates = {
        "EMBEDDING_PROVIDER": config.provider,
        "EMBEDDING_MODEL_NAME": config.model_name,
        "EMBEDDING_API_BASE": config.api_base,
        "EMBEDDING_DEVICE": config.device,
    }
    if config.api_key:
        env_updates["EMBEDDING_API_KEY"] = config.api_key

    # 写入 .env
    if dotenv_path and os.path.exists(dotenv_path):
        try:
            with open(dotenv_path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except Exception:
            lines = []
        existing_keys = set()
        new_lines = []
        for line in lines:
            for key in env_updates:
                if line.startswith(f"{key}="):
                    new_lines.append(f"{key}={env_updates[key]}\n")
                    existing_keys.add(key)
                    break
            else:
                new_lines.append(line.rstrip("\n") + "\n")
        for key, val in env_updates.items():
            if key not in existing_keys:
                new_lines.append(f"{key}={val}\n")
        try:
            with open(dotenv_path, "w", encoding="utf-8") as fh:
                fh.writelines(new_lines)
        except Exception as e:
            logger.warning(f"[EmbeddingAPI] Failed to write .env: {e}")

    # 热重载 Settings
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
async def test_embedding_config(body: EmbeddingConfigBody):
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
    except Exception as e:
        return {"success": False, "latency_ms": 0, "dimension": 0, "error": str(e)}


@router.get("/models")
async def list_embedding_models(provider: str = "", api_base: str = "", api_key: str = ""):
    """拉取嵌入模型列表 (仅 OpenAI 兼容 API 支持)"""
    if not api_base and not provider:
        return {"models": []}
    if provider in ("openai", "") and api_key:
        import httpx

        base = (api_base or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/models"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                headers = {"Authorization": f"Bearer {api_key}"}
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                all_models = [m.get("id", "") for m in data.get("data", [])]
                # 过滤 embedding 模型
                embedding_models = [
                    m for m in all_models if "embed" in m.lower() or "bge" in m.lower()
                ]
                return {"models": embedding_models or all_models[:50]}
        except Exception as e:
            return {"models": [], "error": str(e)}
    return {"models": []}
