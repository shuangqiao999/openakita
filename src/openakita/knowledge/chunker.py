"""文本分块器 — 支持固定大小和按段落分块"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChunkResult:
    """单个分块结果"""

    index: int
    content: str
    token_estimate: int


class TextChunker:
    """文本分块器，支持固定大小分块和按段落分块。

    Usage:
        chunker = TextChunker(strategy="paragraph", max_chunk_size=1000)
        chunks = chunker.chunk(text)
    """

    def __init__(
        self,
        strategy: str = "paragraph",
        chunk_size: int = 500,
        overlap: int = 50,
        max_chunk_size: int = 1000,
    ) -> None:
        self.strategy = strategy
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.max_chunk_size = max_chunk_size

    def chunk(self, text: str) -> list[ChunkResult]:
        if self.strategy == "fixed":
            return self._chunk_fixed(text)
        return self._chunk_paragraph(text)

    def _chunk_fixed(self, text: str) -> list[ChunkResult]:
        chunks: list[ChunkResult] = []
        step = max(1, self.chunk_size - self.overlap)
        start = 0
        i = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunk_text = text[start:end]
            if chunk_text.strip():
                chunks.append(ChunkResult(
                    index=i,
                    content=chunk_text,
                    token_estimate=_estimate_tokens(chunk_text),
                ))
                i += 1
            start += step
        return chunks

    def _chunk_paragraph(self, text: str) -> list[ChunkResult]:
        paragraphs = _split_paragraphs(text)
        chunks: list[ChunkResult] = []
        buffer: list[str] = []
        buffer_len = 0
        index = 0

        def _flush() -> None:
            nonlocal index, buffer_len
            if buffer:
                content = "\n\n".join(buffer)
                chunks.append(ChunkResult(
                    index=index,
                    content=content,
                    token_estimate=_estimate_tokens(content),
                ))
                index += 1
                buffer.clear()
                buffer_len = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_len = len(para)

            if para_len > self.max_chunk_size:
                _flush()
                sub_chunks = self._split_long_text(para)
                for sc in sub_chunks:
                    chunks.append(ChunkResult(
                        index=index,
                        content=sc,
                        token_estimate=_estimate_tokens(sc),
                    ))
                    index += 1
                continue

            if buffer_len + para_len > self.max_chunk_size:
                _flush()

            buffer.append(para)
            buffer_len += para_len

        _flush()
        return chunks

    def _split_long_text(self, text: str) -> list[str]:
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + self.max_chunk_size, len(text))
            if end < len(text):
                break_point = text.rfind("\n", start, end)
                if break_point > start:
                    end = break_point
                else:
                    break_point = text.rfind("。", start, end)
                    if break_point > start:
                        end = break_point + 1
            chunks.append(text[start:end].strip())
            start = end
        return chunks


def _split_paragraphs(text: str) -> list[str]:
    """将文本拆分为段落（以空行分隔）。"""
    import re

    return re.split(r"\n\s*\n", text)


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数量（中文约 1.5 字符/token，英文约 4 字符/token）。"""
    import re

    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    other_chars = len(text) - chinese_chars
    return chinese_chars + (other_chars // 3)
