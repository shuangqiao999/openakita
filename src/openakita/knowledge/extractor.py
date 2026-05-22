"""文档文本提取器 — 从 PDF/Word/Markdown/TXT 提取纯文本"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".md", ".txt", ".markdown"}


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
        return "\n\n".join(texts).strip()
    except ImportError:
        pass
    except Exception:
        pass

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


def _extract_markdown(path: Path) -> str:
    """读取 Markdown 文件的文本（保留原始格式）。"""
    return path.read_text(encoding="utf-8")


def _extract_text(path: Path) -> str:
    """读取纯文本文件。"""
    import chardet

    raw = path.read_bytes()
    result = chardet.detect(raw)
    encoding = result["encoding"] or "utf-8"
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return raw.decode("utf-8", errors="replace")
