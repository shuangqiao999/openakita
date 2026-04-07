"""
统一缓存框架

解决4套独立缓存系统的问题：
- _section_cache (无TTL)
- _static_prompt_cache (300s)
- _agents_md_cache (60s)
- _runtime_section_cache (30s)

统一使用 TTLCache，按类型分类管理。
"""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from cachetools import TTLCache

logger = logging.getLogger(__name__)


class CacheType(Enum):
    """缓存类型枚举"""

    PROMPT = "prompt"
    IDENTITY = "identity"
    SKILL = "skill"
    MCP_CATALOG = "mcp_catalog"
    AGENTS_MD = "agents_md"
    RUNTIME = "runtime"


@dataclass
class CacheConfig:
    """缓存配置"""

    maxsize: int = 100
    ttl: int = 300  # 默认300秒


# 各类缓存的默认配置
_CACHE_CONFIGS: dict[CacheType, CacheConfig] = {
    CacheType.PROMPT: CacheConfig(maxsize=100, ttl=300),
    CacheType.IDENTITY: CacheConfig(maxsize=50, ttl=60),
    CacheType.SKILL: CacheConfig(maxsize=200, ttl=300),
    CacheType.MCP_CATALOG: CacheConfig(maxsize=50, ttl=30),
    CacheType.AGENTS_MD: CacheConfig(maxsize=20, ttl=60),
    CacheType.RUNTIME: CacheConfig(maxsize=100, ttl=30),
}


class UnifiedCache:
    """
    统一缓存管理器

    使用示例:
        from openakita.core.cache import UnifiedCache, CacheType

        # 获取缓存
        value = UnifiedCache.get(CacheType.PROMPT, "system_prompt")

        # 设置缓存
        UnifiedCache.set(CacheType.PROMPT, "system_prompt", "content")

        # 失效缓存
        UnifiedCache.invalidate(CacheType.PROMPT, "system_prompt")

        # 文件变化时自动失效
        UnifiedCache.invalidate_on_file_change(Path("identity/SOUL.md"))
    """

    _caches: dict[CacheType, TTLCache] = {}
    _lock = asyncio.Lock()
    _initialized = False

    @classmethod
    def _ensure_initialized(cls) -> None:
        """确保缓存已初始化"""
        if cls._initialized:
            return
        for cache_type, config in _CACHE_CONFIGS.items():
            cls._caches[cache_type] = TTLCache(maxsize=config.maxsize, ttl=config.ttl)
        cls._initialized = True
        logger.info(f"UnifiedCache initialized with {len(_CACHE_CONFIGS)} cache types")

    @classmethod
    def get(cls, cache_type: CacheType, key: str) -> Any | None:
        """获取缓存值"""
        cls._ensure_initialized()
        cache = cls._caches.get(cache_type)
        if cache is None:
            return None
        return cache.get(key)

    @classmethod
    def set(cls, cache_type: CacheType, key: str, value: Any) -> None:
        """设置缓存值"""
        cls._ensure_initialized()
        cache = cls._caches.get(cache_type)
        if cache is not None:
            cache[key] = value
            logger.debug(f"Cached [{cache_type.value}]: {key}")

    @classmethod
    def get_or_compute(
        cls, cache_type: CacheType, key: str, compute_fn: callable, *args, **kwargs
    ) -> Any:
        """
        获取缓存，如果不存在则计算并缓存

        示例:
            content = UnifiedCache.get_or_compute(
                CacheType.PROMPT,
                "system_prompt",
                lambda: load_system_prompt()
            )
        """
        cls._ensure_initialized()
        cache = cls._caches.get(cache_type)
        if cache is None:
            return compute_fn(*args, **kwargs)

        if key in cache:
            return cache[key]

        # 计算并缓存
        value = compute_fn(*args, **kwargs)
        cache[key] = value
        return value

    @classmethod
    def invalidate(cls, cache_type: CacheType, key: str | None = None) -> None:
        """失效缓存"""
        cls._ensure_initialized()
        if key is None:
            # 清空整个类型缓存
            cache = cls._caches.get(cache_type)
            if cache:
                cache.clear()
                logger.info(f"Cleared cache [{cache_type.value}]")
        else:
            # 删除特定key
            cache = cls._caches.get(cache_type)
            if cache:
                cache.pop(key, None)
                logger.debug(f"Invalidated [{cache_type.value}]: {key}")

    @classmethod
    def invalidate_pattern(cls, cache_type: CacheType, pattern: str) -> int:
        """按模式批量失效缓存（支持通配符*）"""
        cls._ensure_initialized()
        cache = cls._caches.get(cache_type)
        if not cache:
            return 0

        removed = 0
        keys_to_remove = []

        for key in cache.keys():
            if "*" in pattern:
                import fnmatch

                if fnmatch.fnmatch(key, pattern):
                    keys_to_remove.append(key)
            elif pattern in key:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            cache.pop(key, None)
            removed += 1

        if removed > 0:
            logger.info(f"Invalidated {removed} keys matching [{cache_type.value}]: {pattern}")

        return removed

    @classmethod
    def invalidate_on_file_change(cls, file_path: Path) -> None:
        """
        文件变化时自动失效相关缓存

        根据文件路径判断应该失效哪些缓存类型:
        - identity/*.md -> IDENTITY, PROMPT
        - skills/**/*.md -> SKILL
        - AGENTS.md -> AGENTS_MD
        """
        cls._ensure_initialized()
        path_str = str(file_path)

        invalidated_types: list[CacheType] = []

        if "identity" in path_str and file_path.suffix == ".md":
            invalidated_types.extend([CacheType.IDENTITY, CacheType.PROMPT])
        elif "skills" in path_str and file_path.suffix == ".md":
            invalidated_types.append(CacheType.SKILL)
        elif "AGENTS.md" in path_str:
            invalidated_types.append(CacheType.AGENTS_MD)

        for cache_type in invalidated_types:
            cls.invalidate(cache_type)

        if invalidated_types:
            logger.info(f"Cache invalidated for file change: {file_path.name}")

    @classmethod
    def get_stats(cls) -> dict[str, Any]:
        """获取缓存统计信息"""
        cls._ensure_initialized()
        stats = {}
        for cache_type, cache in cls._caches.items():
            stats[cache_type.value] = {
                "size": len(cache),
                "maxsize": cache.maxsize,
                "ttl": cache.ttl,
            }
        return stats

    @classmethod
    async def invalidate_async(cls, cache_type: CacheType, key: str | None = None) -> None:
        """异步失效缓存"""
        async with cls._lock:
            cls.invalidate(cache_type, key)


# 便捷函数
def clear_prompt_cache() -> None:
    """清除Prompt缓存（兼容旧API）"""
    UnifiedCache.invalidate(CacheType.PROMPT)


def clear_identity_cache() -> None:
    """清除Identity缓存"""
    UnifiedCache.invalidate(CacheType.IDENTITY)


def clear_skill_cache() -> None:
    """清除Skill缓存"""
    UnifiedCache.invalidate(CacheType.SKILL)
