"""知识库管理 API 路由

POST   /api/kb/upload          — 上传文档
GET    /api/kb/documents        — 文档列表
DELETE /api/kb/documents/{id}   — 删除文档
POST   /api/kb/search           — 搜索知识库
GET    /api/kb/status/{id}      — 文档处理状态
GET    /api/kb/ready            — 就绪检查
POST   /api/kb/repair/{id}      — 修复文档一致性
GET    /api/kb/verify/{id}      — 验证文档一致性
GET    /api/kb/inconsistent     — 列出不一致文档
GET    /api/kb/graph            — 图谱数据（节点+边）
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kb", tags=["知识库"])

_KB_MAX_UPLOAD_SIZE = 20 * 1024 * 1024
_ALLOWED_EXTENSIONS = {
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


def _get_kb_manager(request: Request):
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    kb = getattr(agent, "kb_manager", None)
    if kb is None:
        raise HTTPException(status_code=503, detail="知识库功能未初始化")
    return kb


def _get_kb_manager_optional(request: Request):
    """获取 KB 管理器，不抛异常。用于就绪检查等不需要强制 KB 可用的场景。"""
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return None
    return getattr(agent, "kb_manager", None)


class SearchRequest(BaseModel):
    query: str = Field(..., description="搜索查询")
    top_k: int = Field(5, ge=1, le=20, description="返回结果数")
    doc_filter: str | None = Field(None, description="按文档 ID 过滤")


@router.post("/upload", summary="上传文档")
async def upload_document(request: Request, file: UploadFile = File(...)):
    """上传文档到知识库，返回 doc_id，后台处理文档。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {suffix}，支持: {sorted(_ALLOWED_EXTENSIONS)}",
        )

    kb = _get_kb_manager(request)

    fd, tmp_path = tempfile.mkstemp(suffix=suffix, dir=str(kb._tmp_dir))
    try:
        try:
            total = 0
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _KB_MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"文件超过大小限制 ({_KB_MAX_UPLOAD_SIZE // 1024 // 1024} MB)",
                    )
                os.write(fd, chunk)
        finally:
            os.close(fd)

        upload_result = await kb.upload_document(tmp_path, display_name=file.filename)

        async def _cleanup():
            await asyncio.sleep(5)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        asyncio.create_task(_cleanup())

        if upload_result.get("duplicate"):
            return {
                "status": "duplicate",
                "doc_id": None,
                "existing_doc_id": upload_result["existing_doc_id"],
                "existing_name": upload_result["existing_name"],
                "existing_status": upload_result["existing_status"],
            }
        return {"status": "ok", "doc_id": upload_result["doc_id"]}
    except HTTPException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        logger.error("[KB] Upload failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/replace", summary="覆盖已存在文档")
async def replace_document(
    request: Request,
    existing_doc_id: str = "",
    file: UploadFile = File(...),
):
    """上传并覆盖已有文档。existing_doc_id 通过 query 参数传入。"""
    if not existing_doc_id:
        raise HTTPException(status_code=400, detail="缺少 existing_doc_id 参数")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {suffix}")

    kb = _get_kb_manager(request)
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, dir=str(kb._tmp_dir))
    try:
        try:
            total = 0
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _KB_MAX_UPLOAD_SIZE:
                    raise HTTPException(status_code=400, detail="文件超过大小限制")
                os.write(fd, chunk)
        finally:
            os.close(fd)

        result = await kb.replace_document(existing_doc_id, tmp_path, display_name=file.filename)

        async def _cleanup():
            await asyncio.sleep(5)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        asyncio.create_task(_cleanup())

        return {"status": "ok", "doc_id": result["doc_id"]}
    except HTTPException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        logger.error("[KB] Upload failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/documents", summary="文档列表")
async def list_documents(
    request: Request,
    limit: int = 20,
    offset: int = 0,
):
    """分页获取文档列表。"""
    kb = _get_kb_manager(request)
    result = await kb.list_documents(limit=limit, offset=offset)
    return result


@router.delete("/documents/{doc_id}", summary="删除文档")
async def delete_document(request: Request, doc_id: str):
    """删除文档及其所有分块。"""
    kb = _get_kb_manager(request)
    deleted = await kb.delete_document(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"status": "ok", "doc_id": doc_id}


@router.post("/search", summary="搜索知识库")
async def search_knowledge(request: Request, body: SearchRequest):
    """在知识库中搜索相关内容。"""
    kb = _get_kb_manager(request)
    results = await kb.search(
        query=body.query,
        top_k=body.top_k,
        doc_filter=body.doc_filter,
    )
    return {"status": "ok", "results": results, "query": body.query}


@router.get("/ready", summary="知识库就绪检查")
async def kb_ready(request: Request):
    """检查知识库是否可用（嵌入模型已配置 + 向量表存在）。"""
    kb = _get_kb_manager_optional(request)
    if kb is None:
        return {"ready": False, "reason": "知识库模块未初始化"}
    return {"ready": kb.is_ready()}


@router.get("/status/{doc_id}", summary="文档状态与分块详情")
async def document_status(request: Request, doc_id: str):
    """查询文档处理状态及分块内容。"""
    kb = _get_kb_manager(request)
    doc_status = await kb.get_document_by_id(doc_id)
    if doc_status is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc_status


@router.get("/documents/{doc_id}/content", summary="文档全文内容")
async def document_content(request: Request, doc_id: str):
    """获取文档完整内容（拼接所有分块）。"""
    kb = _get_kb_manager(request)
    doc = await kb.get_document_full_content(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


class UpdateDocumentRequest(BaseModel):
    content: str = Field(..., min_length=1, description="编辑后的文档全文")
    name: str | None = Field(None, description="可选的新文件名")


@router.put("/documents/{doc_id}", summary="编辑文档全文")
async def update_document(request: Request, doc_id: str, body: UpdateDocumentRequest):
    """编辑文档全文并重新入库（删除旧分块 → 重新分块 → 嵌入 → 存储）。"""
    kb = _get_kb_manager(request)
    try:
        result = await kb.update_document_content(doc_id, body.content, body.name)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"编辑失败: {e}")


@router.post("/repair/{doc_id}", summary="修复文档一致性")
async def repair_document(request: Request, doc_id: str):
    """从 SQLite 分块重建 LanceDB 向量索引。"""
    kb = _get_kb_manager(request)
    result = await kb.repair_document(doc_id)
    if not result.get("repaired"):
        return {"status": "skipped", "reason": result.get("reason", "未知原因")}
    return {"status": "ok", "chunks": result["chunks"]}


@router.get("/verify/{doc_id}", summary="验证文档一致性")
async def verify_document(request: Request, doc_id: str):
    """对比 SQLite 分块数与 LanceDB 向量数。"""
    kb = _get_kb_manager(request)
    result = await kb.verify_document(doc_id)
    if result is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return result


@router.get("/inconsistent", summary="列出不一致文档")
async def list_inconsistent(request: Request):
    """列出 SQLite 与 LanceDB 记录数不匹配的文档。"""
    kb = _get_kb_manager(request)
    docs = await kb.get_inconsistent_documents()
    return {"inconsistent": docs, "count": len(docs)}


@router.post("/repair-orphans", summary="清理孤儿向量")
async def repair_orphans(request: Request):
    """清理 LanceDB 中无对应 SQLite 文档的孤儿向量。"""
    kb = _get_kb_manager(request)
    result = await kb.repair_orphan_vectors()
    return result


@router.get("/graph", summary="知识库图谱数据")
async def get_graph(
    request: Request,
    doc_id: str | None = None,
    include_semantic: bool = False,
    similarity_threshold: float = 0.75,
    max_nodes: int = 0,
):
    """获取图谱节点和边数据，供 3D 力导向图渲染。max_nodes=0 表示不限制。"""
    kb = _get_kb_manager(request)
    data = await kb.get_graph_data(
        doc_id=doc_id,
        include_semantic=include_semantic,
        similarity_threshold=similarity_threshold,
        max_nodes=max_nodes,
    )
    return data


class IngestRequest(BaseModel):
    title: str = Field(..., description="文档标题")
    content: str = Field(..., min_length=1, description="文本内容")
    file_type: str = Field("web", description="文档类型标签")


@router.post("/ingest", summary="文本入库")
async def ingest_text(request: Request, body: IngestRequest):
    """将纯文本内容（网页、对话片段等）直接保存到知识库。"""
    kb = _get_kb_manager(request)
    result = await kb.ingest_text(body.title, body.content, body.file_type)
    return result
