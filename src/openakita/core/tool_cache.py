"""
智能工具缓存 - 加速重复调用
"""

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from difflib import SequenceMatcher


@dataclass
class CacheEntry:
    """缓存条目"""

    tool_name: str
    params: dict
    timestamp: float
    hit_count: int = 0
    last_access: float = field(default_factory=time.time)
    original_text: str = ""


class SemanticStringMatcher:
    """轻量级语义字符串匹配"""

    def __init__(self, threshold: float = 0.8):
        self.threshold = threshold

    def similarity(self, a: str, b: str) -> float:
        if not a or not b:
            return 0.0

        ratio = SequenceMatcher(None, a.lower(), b.lower()).ratio()

        if a.lower() in b.lower() or b.lower() in a.lower():
            ratio = max(ratio, 0.85)

        return ratio

    def is_similar(self, a: str, b: str) -> bool:
        return self.similarity(a, b) >= self.threshold


class SmartToolCache:
    """智能工具缓存 - LRU + 语义相似匹配"""

    def __init__(self, max_size: int = 200, similarity_threshold: float = 0.6):
        self.max_size = max_size
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.semantic_matcher = SemanticStringMatcher(similarity_threshold)
        self._stats = {"hits": 0, "misses": 0, "semantic_hits": 0, "evictions": 0}

    def _get_key(self, text: str, context: str | None = None) -> str:
        key = text.lower().strip()
        if context:
            key = f"{context}:{key}"
        return hashlib.md5(key.encode()).hexdigest()

    def get(self, text: str, context: str | None = None) -> tuple[str, dict] | None:
        """获取缓存"""
        original_key = self._get_key(text, context)

        if original_key in self.cache:
            entry = self.cache[original_key]
            entry.hit_count += 1
            entry.last_access = time.time()
            self.cache.move_to_end(original_key)
            self._stats["hits"] += 1
            return (entry.tool_name, entry.params.copy())

        best_match = None
        best_score = 0

        for key, entry in self.cache.items():
            if hasattr(entry, "original_text") and entry.original_text:
                score = self.semantic_matcher.similarity(text, entry.original_text)
                if score > best_score and score >= self.semantic_matcher.threshold:
                    best_score = score
                    best_match = (key, entry)

        if best_match:
            key, entry = best_match
            entry.hit_count += 1
            entry.last_access = time.time()
            self.cache.move_to_end(key)
            self._stats["semantic_hits"] += 1
            return (entry.tool_name, entry.params.copy())

        self._stats["misses"] += 1
        return None

    def set(self, text: str, tool_name: str, params: dict, context: str | None = None):
        """设置缓存"""
        key = self._get_key(text, context)

        if len(self.cache) >= self.max_size and self.cache:
            self.cache.popitem(last=False)
            self._stats["evictions"] += 1

        entry = CacheEntry(
            tool_name=tool_name, params=params, timestamp=time.time(), original_text=text
        )

        self.cache[key] = entry
        self.cache.move_to_end(key)

    def get_hot_tools(self, top_k: int = 10) -> list[tuple[str, int]]:
        """获取热点工具"""
        tool_hits = {}
        for entry in self.cache.values():
            tool_hits[entry.tool_name] = tool_hits.get(entry.tool_name, 0) + entry.hit_count

        sorted_tools = sorted(tool_hits.items(), key=lambda x: x[1], reverse=True)
        return sorted_tools[:top_k]

    def clear_old(self, max_age_seconds: int = 3600) -> int:
        """清理过期缓存"""
        now = time.time()
        expired_keys = [
            key for key, entry in self.cache.items() if now - entry.timestamp > max_age_seconds
        ]
        for key in expired_keys:
            del self.cache[key]
            self._stats["evictions"] += 1

        return len(expired_keys)

    def get_stats(self) -> dict:
        """获取统计信息"""
        total = self._stats["hits"] + self._stats["misses"]
        return {
            "cache_size": len(self.cache),
            "max_size": self.max_size,
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "hit_rate": self._stats["hits"] / total if total > 0 else 0,
            "semantic_hits": self._stats["semantic_hits"],
            "evictions": self._stats["evictions"],
            "hot_tools": self.get_hot_tools(5),
        }
