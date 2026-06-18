"""
共享中文分词工具模块

提供统一的 jieba 分词入口，懒加载 + 回退策略。
项目中所有需要中文分词的地方应统一调用此模块，避免分散实现。
"""

from __future__ import annotations

import logging
import re
from typing import Any

_jieba_mod: Any = None
_jieba_checked = False

_logger = logging.getLogger(__name__)


def _ensure_jieba() -> Any:
    global _jieba_mod, _jieba_checked  # noqa: PLW0603
    if not _jieba_checked:
        try:
            import jieba

            jieba.setLogLevel(logging.WARNING)
            _jieba_mod = jieba
        except ImportError:
            _logger.debug("jieba not installed, falling back to regex tokenizer")
        _jieba_checked = True
    return _jieba_mod


def tokenize_words(text: str) -> set[str]:
    """将文本分词为词集合 (用于去重/匹配/Jaccard/关键词提取)。

    使用 jieba cut_for_search 分词，回退到英文单词 + CJK 双字提取。
    过滤长度 < 2 的 token。
    """
    if not text or not text.strip():
        return set()
    lowered = text.lower()
    _jb = _ensure_jieba()
    if _jb is not None:
        return {w for w in _jb.cut_for_search(lowered) if len(w) >= 2}
    en = set(re.findall(r"[a-zA-Z]\w+", lowered))
    cjk = set(re.findall(r"[\u4e00-\u9fff]{2,}", lowered))
    return en | cjk


def segment_text(text: str) -> str:
    """将文本分词为空格连接的字符串 (用于 FTS5 索引/SimHash)。

    使用 jieba cut_for_search 分词，回退到原文。
    """
    if not text:
        return ""
    _jb = _ensure_jieba()
    if _jb is not None:
        return " ".join(_jb.cut_for_search(text))
    return text


def extract_keywords(text: str, top_k: int = 5) -> list[str]:
    """从文本中提取关键词，按长度降序返回。

    使用 jieba 分词后取最长的 top_k 个词。
    """
    if not text or not text.strip():
        return []
    words = tokenize_words(text)
    ranked = sorted(words, key=len, reverse=True)
    return ranked[:top_k]
