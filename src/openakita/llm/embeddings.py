"""
嵌入模型抽象层

提供统一的嵌入接口，支持:
- OpenAI 兼容 API (text-embedding-ada-002 / text-embedding-3-small 等)
- HuggingFace sentence-transformers 本地模型 (BAAI/bge-small-zh 等)
- 自定义 API 端点

工厂函数 get_embedding_model() 根据全局配置自动选择合适的实现。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from openakita.memory.json_utils import coerce_text

logger = logging.getLogger(__name__)

# 全局缓存单例
_EMBEDDING_MODEL_CACHE: dict[str, BaseEmbedding] = {}


class EmbeddingModelError(Exception):
    """嵌入模型初始化或调用失败"""


class BaseEmbedding(ABC):
    """嵌入模型抽象基类"""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """对文本列表进行向量嵌入"""

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]

    @property
    @abstractmethod
    def dimension(self) -> int:
        """返回嵌入向量的维度"""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """返回提供商标识"""


class OpenAIEmbedding(BaseEmbedding):
    """OpenAI 兼容 API 嵌入"""

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_base: str | None = None,
        api_key: str | None = None,
        dimension: int = 1536,
    ):
        self._model_name = model_name
        self._api_base = (api_base or "").rstrip("/")
        self._api_key = api_key
        self._dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        url = f"{self._api_base}/embeddings" if self._api_base else "https://api.openai.com/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "input": texts,
            "model": self._model_name,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            embeddings = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
            return [e.get("embedding", []) for e in embeddings]

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def provider_name(self) -> str:
        return "openai"


class HuggingFaceEmbedding(BaseEmbedding):
    """HuggingFace Sentence-Transformers 本地嵌入"""

    def __init__(self, model_name: str = "BAAI/bge-small-zh", device: str = "cpu"):
        self._model_name = model_name
        self._device = device
        self._model: object | None = None
        self._dimension = 0

    async def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer

            self._model = await asyncio.to_thread(
                SentenceTransformer, self._model_name, device=self._device
            )
            self._dimension = self._model.get_sentence_embedding_dimension() or 768
        except ImportError:
            raise EmbeddingModelError(
                "sentence-transformers 未安装。请运行: pip install sentence-transformers"
            ) from None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        await self._ensure_model()
        assert self._model is not None
        embeddings = await asyncio.to_thread(
            self._model.encode, texts, show_progress_bar=False, normalize_embeddings=True
        )
        return [e.tolist() for e in embeddings]

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def provider_name(self) -> str:
        return "huggingface"


class CustomEmbedding(BaseEmbedding):
    """自定义 API 端点嵌入"""

    def __init__(self, api_base: str, api_key: str = "", model_name: str = "", dimension: int = 768):
        self._api_base = api_base.rstrip("/")
        self._api_key = api_key
        self._model_name = model_name
        self._dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        url = f"{self._api_base}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "input": texts,
            "model": self._model_name or "default",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            embeddings = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
            return [e.get("embedding", []) for e in embeddings]

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def provider_name(self) -> str:
        return "custom"


def _build_embedding_config() -> dict:
    """从全局 Settings 构建嵌入模型配置 dict"""
    try:
        from openakita.config import settings

        return {
            "provider": getattr(settings, "embedding_provider", "") or "",
            "api_base": getattr(settings, "embedding_api_base", "") or "",
            "api_key": getattr(settings, "embedding_api_key", "") or "",
            "model_name": getattr(settings, "embedding_model_name", "") or "",
            "device": getattr(settings, "embedding_device", "cpu") or "cpu",
        }
    except Exception:
        return {}


def get_embedding_model(config: dict | None = None) -> BaseEmbedding:
    """获取嵌入模型实例 (单例缓存)

    Args:
        config: 可选配置 dict，包含 provider / api_base / api_key / model_name / device
                为空时从全局 Settings 读取

    Returns:
        BaseEmbedding 实例

    Raises:
        EmbeddingModelError: 当配置无效或模型无法初始化时
    """
    if config is None:
        config = _build_embedding_config()

    provider = (config.get("provider") or "").strip().lower()
    cache_key = f"{provider}|{config.get('model_name','')}|{config.get('api_base','')}"

    if cache_key in _EMBEDDING_MODEL_CACHE:
        return _EMBEDDING_MODEL_CACHE[cache_key]

    # 未配置 embedding_provider 时，尝试复用 chat endpoint 的 OpenAI 凭据
    if not provider:
        try:
            from openakita.llm.runtime_config import get_active_chat_endpoint

            ep = get_active_chat_endpoint()
            if ep:
                provider = "openai"
                config = {
                    "provider": "openai",
                    "api_base": getattr(ep, "base_url", ""),
                    "api_key": getattr(ep, "api_key", ""),
                    "model_name": "text-embedding-3-small",
                    "device": "cpu",
                }
        except Exception:
            pass

    if not provider or not config.get("model_name", ""):
        raise EmbeddingModelError("嵌入模型未配置。请在 LLM 设置中配置嵌入模型。")

    model: BaseEmbedding
    if provider == "openai":
        model = OpenAIEmbedding(
            model_name=config.get("model_name", "text-embedding-3-small"),
            api_base=config.get("api_base", "") or None,
            api_key=config.get("api_key", "") or None,
        )
    elif provider == "huggingface":
        model = HuggingFaceEmbedding(
            model_name=config["model_name"],
            device=config.get("device", "cpu"),
        )
    elif provider == "custom":
        model = CustomEmbedding(
            api_base=config["api_base"],
            api_key=config.get("api_key", ""),
            model_name=config.get("model_name", ""),
        )
    else:
        raise EmbeddingModelError(f"不支持的嵌入模型提供商: {provider}")

    _EMBEDDING_MODEL_CACHE[cache_key] = model
    logger.info(
        f"[Embedding] Initialized {provider} embedding model: "
        f"{config.get('model_name')} (dim={model.dimension})"
    )
    return model


def clear_embedding_cache() -> None:
    """清除嵌入模型缓存 (热重载时调用)"""
    _EMBEDDING_MODEL_CACHE.clear()
    logger.info("[Embedding] Model cache cleared")


async def test_embedding_model(config: dict) -> dict:
    """测试嵌入模型连接

    Args:
        config: 嵌入模型配置

    Returns:
        {"success": bool, "latency_ms": float, "dimension": int, "error": str | None}
    """
    import time

    clear_embedding_cache()
    try:
        model = get_embedding_model(config)
        t0 = time.monotonic()
        vec = await model.embed_query(_TEST_PHRASE)
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "success": True,
            "latency_ms": round(elapsed, 1),
            "dimension": len(vec) if vec else 0,
            "error": None,
        }
    except Exception as e:
        return {
            "success": False,
            "latency_ms": 0,
            "dimension": 0,
            "error": coerce_text(e),
        }


_TEST_PHRASE = "Hello world 你好世界"
