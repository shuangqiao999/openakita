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

logger = logging.getLogger(__name__)

# 全局缓存单例 — 由 _cache_lock 保护
_EMBEDDING_MODEL_CACHE: dict[str, BaseEmbedding] = {}
_cache_lock = asyncio.Lock()
_embed_lock = asyncio.Lock()

# 测试用短语 (在使用前定义)
_TEST_PHRASE = "Hello world 你好世界"


class EmbeddingModelError(Exception):
    """嵌入模型初始化或调用失败"""


class BaseEmbedding(ABC):
    """嵌入模型抽象基类"""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """对文本列表进行向量嵌入"""

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed([text])
        if not results:
            raise EmbeddingModelError("embed() returned empty result")
        return results[0]

    @property
    @abstractmethod
    def dimension(self) -> int:
        """返回嵌入向量的维度"""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """返回提供商标识"""

    @staticmethod
    def _parse_embedding_response(
        data: dict, expected_count: int
    ) -> list[list[float]]:
        """从 OpenAI 兼容 API 响应中解析嵌入向量并验证"""
        items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        embeddings = []
        for item in items[:expected_count]:
            vec = item.get("embedding")
            if not vec or not isinstance(vec, list):
                raise EmbeddingModelError("API 返回了空的嵌入向量")
            embeddings.append(vec)
        if len(embeddings) != expected_count:
            raise EmbeddingModelError(
                f"嵌入数量不匹配: 期望 {expected_count}, 实际 {len(embeddings)}"
            )
        return embeddings


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
        payload = {"input": texts, "model": self._model_name}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            return self._parse_embedding_response(data, len(texts))
        except EmbeddingModelError:
            raise
        except Exception as e:
            raise EmbeddingModelError(f"OpenAI 嵌入请求失败: {e}") from e

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
        self._init_lock = asyncio.Lock()

    async def _ensure_model(self) -> None:
        if self._model is not None:
            return
        async with self._init_lock:
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
        try:
            embeddings = await asyncio.to_thread(
                self._model.encode, texts, show_progress_bar=False, normalize_embeddings=True
            )
            return [e.tolist() for e in embeddings]
        except Exception as e:
            raise EmbeddingModelError(f"HuggingFace 嵌入失败: {e}") from e

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
        payload = {"input": texts, "model": self._model_name or "default"}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            return self._parse_embedding_response(data, len(texts))
        except EmbeddingModelError:
            raise
        except Exception as e:
            raise EmbeddingModelError(f"自定义嵌入请求失败: {e}") from e

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def provider_name(self) -> str:
        return "custom"


def _read_embedding_endpoint_from_config() -> dict | None:
    """从 llm_endpoints.json 的 embedding_endpoints 中读取活跃端点"""
    try:
        from openakita.llm.config import get_default_config_path
        from openakita.utils.atomic_io import read_json_safe

        config_path = get_default_config_path()
        data = read_json_safe(config_path)
        if not data:
            return None

        emb_eps = data.get("embedding_endpoints", [])
        for ep_data in emb_eps:
            if ep_data.get("enabled", True) is False:
                continue
            provider = (ep_data.get("provider", "") or "").lower()
            base_url = (ep_data.get("base_url", "") or "").strip()
            api_key_env = ep_data.get("api_key_env", "")
            model = ep_data.get("model", "")

            # 解析 API key
            api_key = ep_data.get("api_key") or ""
            if not api_key and api_key_env:
                import os

                api_key = os.environ.get(api_key_env, "")

            if provider and model:
                return {
                    "provider": provider,
                    "model_name": model,
                    "api_base": base_url,
                    "api_key": api_key,
                    "device": "cpu",
                }
        return None
    except Exception:
        logger.exception("[Embedding] Failed to read embedding endpoint config")
        return None


def _build_embedding_config() -> dict:
    """构建嵌入模型配置 dict，优先从 embedding_endpoints 读取，fallback 到 .env"""
    # 优先: embedding_endpoints (新端点体系)
    ep_config = _read_embedding_endpoint_from_config()
    if ep_config:
        return ep_config

    # fallback: 旧 .env 配置 (向后兼容)
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
        logger.exception("[Embedding] Failed to read embedding config from Settings")
        return {}


def _infer_embedding_from_chat_endpoint(config: dict) -> dict | None:
    """尝试从全局配置推断嵌入凭据 (OpenAI 兼容 API)"""
    try:
        from openakita.config import settings

        api_key = getattr(settings, "embedding_api_key", "") or ""
        if not api_key:
            api_key = getattr(settings, "openai_api_key", "") or ""
        if not api_key:
            return None

        api_base = getattr(settings, "embedding_api_base", "") or ""
        if not api_base:
            api_base = getattr(settings, "llm_endpoint_base_url", "") or ""
        if api_base and api_key:
            return {
                "provider": "openai",
                "api_base": api_base.rstrip("/"),
                "api_key": api_key,
                "model_name": config.get("model_name", "text-embedding-3-small") or "text-embedding-3-small",
                "device": "cpu",
            }
        return None
    except Exception:
        return None


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
    model_key = config.get("model_name", "") or ""
    base_key = config.get("api_base", "") or ""
    cache_key = f"{provider}|{model_key}|{base_key}"

    # 快速路径 (无需锁)
    if cache_key in _EMBEDDING_MODEL_CACHE:
        return _EMBEDDING_MODEL_CACHE[cache_key]

    # 未配置 embedding_provider 时，尝试从全局凭据推断
    if not provider:
        inferred = _infer_embedding_from_chat_endpoint(config)
        if inferred:
            config = inferred
            provider = "openai"
            model_key = config.get("model_name", "")
            base_key = config.get("api_base", "")
            cache_key = f"{provider}|{model_key}|{base_key}"

    if not provider or not config.get("model_name", ""):
        raise EmbeddingModelError("嵌入模型未配置。请在 LLM 设置中配置嵌入模型。")

    # 加锁创建 (防并发重复初始化)
    async def _locked_init():
        async with _cache_lock:
            if cache_key in _EMBEDDING_MODEL_CACHE:
                return _EMBEDDING_MODEL_CACHE[cache_key]

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

            # Auto-discover true dimension from API response (not hardcoded default)
            if provider in ("openai", "custom"):
                try:
                    test_vec = await model.embed_query("dimension probe")
                    if test_vec:
                        model._dimension = len(test_vec)
                        logger.debug(
                            f"[Embedding] Auto-discovered dimension={model._dimension} "
                            f"for {provider}/{config.get('model_name')}"
                        )
                except Exception:
                    logger.debug(
                        f"[Embedding] Dimension discovery skipped, using default "
                        f"dim={model.dimension} for {provider}"
                    )

            _EMBEDDING_MODEL_CACHE[cache_key] = model
            logger.info(
                f"[Embedding] Initialized {provider} embedding model: "
                f"{config.get('model_name')} (dim={model.dimension})"
            )
            return model

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(_locked_init(), loop)
            return future.result(timeout=60)
        else:
            return asyncio.run(_locked_init())
    except RuntimeError:
        return asyncio.run(_locked_init())


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
        vec = await asyncio.wait_for(
            model.embed_query(_TEST_PHRASE), timeout=60.0
        )
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "success": True,
            "latency_ms": round(elapsed, 1),
            "dimension": len(vec) if vec else 0,
            "error": None,
        }
    except TimeoutError:
        return {
            "success": False,
            "latency_ms": 0,
            "dimension": 0,
            "error": "嵌入模型测试超时 (60s)",
        }
    except Exception as e:
        from openakita.memory.json_utils import coerce_text

        return {
            "success": False,
            "latency_ms": 0,
            "dimension": 0,
            "error": coerce_text(e),
        }
