"""
嵌入模型工具端点

嵌入模型的配置管理已迁移到统一的 llm_endpoints.json (通过 /api/config/save-endpoint 管理)。
此文件仅保留测试连接和模型列表拉取的辅助端点。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from openakita.llm.embeddings import test_embedding_model

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/embedding", tags=["嵌入模型"])


class EmbedTestBody(BaseModel):
    provider: str = ""
    model_name: str = ""
    api_base: str = ""
    api_key: str = ""
    device: str = "cpu"


class FetchModelsBody(BaseModel):
    provider: str = ""
    api_base: str = ""
    api_key: str = ""


@router.post("/test")
async def test_embedding_endpoint(body: EmbedTestBody):
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
    """拉取嵌入模型列表 (仅 OpenAI 兼容 API 支持)。API key 通过 POST body 传递"""
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
