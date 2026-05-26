"""文档文本提取器 — 从 PDF/Word/Markdown/TXT 提取纯文本"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".md", ".txt", ".markdown",
    ".rst", ".org", ".tex", ".html", ".htm", ".csv", ".log",
    ".py", ".pyi", ".pyx", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".scala",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh", ".hxx",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".sql", ".r",
    ".lua", ".dart", ".nim", ".zig", ".ex", ".exs",
    ".sh", ".bash", ".zsh", ".ps1", ".psm1", ".bat", ".cmd",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".env", ".properties", ".editorconfig",
}


def extract_text(file_path: str | Path) -> str:
    """根据文件后缀提取纯文本。

    Args:
        file_path: 文件路径

    Returns:
        提取的纯文本字符串

    Raises:
        ValueError: 不支持的文件类型
        FileNotFoundError: 文件不存在
        RuntimeError: 提取失败
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    suffix = path.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件类型: {suffix}，支持的格式: {sorted(ALLOWED_EXTENSIONS)}")

    if suffix == ".pdf":
        return _extract_pdf(path)
    elif suffix == ".docx":
        return _extract_docx(path)
    elif suffix in {".md", ".markdown"}:
        return _extract_markdown(path)
    else:
        return _extract_text(path)


def _extract_pdf(path: Path) -> str:
    """从 PDF 文件中提取文本，优先使用 pdfplumber，回退到 pypdf。"""
    try:
        import pdfplumber

        texts: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    texts.append(page_text)
        result = "\n\n".join(texts).strip()
        if result:
            return result
        logger.debug("[extractor] pdfplumber returned empty result, falling back to pypdf")
    except ImportError:
        pass
    except Exception as e:
        logger.debug("[extractor] pdfplumber failed, falling back to pypdf: %s", e)

    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        texts: list[str] = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                texts.append(page_text)
        return "\n\n".join(texts).strip()
    except ImportError:
        raise RuntimeError(
            "需要安装 pypdf 或 pdfplumber 来解析 PDF 文件。"
            "请运行: pip install pypdf"
        )
    except Exception as e:
        raise RuntimeError(f"PDF 解析失败: {e}")


def _extract_docx(path: Path) -> str:
    """从 Word 文档中提取文本。"""
    try:
        from docx import Document

        doc = Document(path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)
    except ImportError:
        raise RuntimeError(
            "需要安装 python-docx 来解析 Word 文件。"
            "请运行: pip install python-docx"
        )
    except Exception as e:
        raise RuntimeError(f"Word 文档解析失败: {e}")


def _extract_plain_text(path: Path) -> str:
    """读取纯文本文件（自动检测编码，Markdown/TXT 等通用）。"""
    try:
        import chardet

        raw = path.read_bytes()
        result = chardet.detect(raw)
        encoding = result["encoding"] or "utf-8"
    except ImportError:
        raw = path.read_bytes()
        encoding = "utf-8"
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return raw.decode("utf-8", errors="replace")


# 保留别名以兼容旧引用
_extract_markdown = _extract_plain_text
_extract_text = _extract_plain_text
