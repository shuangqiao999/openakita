"""Deduction Engine API routes — REST + SSE streaming."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/deduction", tags=["deduction"])

_DEDUCTION_MAX_UPLOAD = 20 * 1024 * 1024
_ALLOWED_EXT = {
    ".txt", ".md", ".markdown", ".json", ".yaml", ".yml",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".rs", ".go", ".java", ".c", ".cpp", ".h",
    ".pdf", ".docx", ".csv", ".log", ".rst", ".html", ".htm",
}


def _extract_text(file_path: str, suffix: str) -> str:
    """Extract text from uploaded file. Returns max 100KB of text."""
    path = Path(file_path)
    text_exts = {
        ".txt", ".md", ".markdown", ".json", ".yaml", ".yml",
        ".py", ".js", ".ts", ".jsx", ".tsx", ".rs", ".go", ".java", ".c", ".cpp",
        ".h", ".csv", ".log", ".rst", ".html", ".htm",
    }
    if suffix in text_exts:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:100_000]
        except Exception:
            return path.read_text(encoding="gbk", errors="replace")[:100_000]

    if suffix == ".pdf":
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(file_path)
            text = "\n".join(p.extract_text() or "" for p in reader.pages)
            return text[:100_000]
        except ImportError:
            raise HTTPException(501, "PDF parsing requires PyPDF2 (pip install PyPDF2)")

    if suffix == ".docx":
        try:
            from docx import Document
            doc = Document(file_path)
            text = "\n".join(p.text for p in doc.paragraphs)
            return text[:100_000]
        except ImportError:
            raise HTTPException(501, "DOCX parsing requires python-docx (pip install python-docx)")

    raise HTTPException(400, f"Unsupported file type: {suffix}")


class CreateSessionRequest(BaseModel):
    title: str = Field(default="", description="会话标题")
    source_material: str = Field(default="", description="种子材料/原文")
    config: dict[str, Any] = Field(default_factory=dict)


class InjectEventRequest(BaseModel):
    event_description: str = Field(default="", description="要注入的事件描述")


# ── File upload ──

@router.post("/upload")
async def upload_source_file(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_EXT:
        raise HTTPException(400, f"不支持的文件类型: {suffix}")
    if not file.filename:
        raise HTTPException(400, "文件名为空")

    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        total = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > _DEDUCTION_MAX_UPLOAD:
                raise HTTPException(400, f"文件超过 20MB 限制")
            os.write(fd, chunk)
    finally:
        os.close(fd)

    try:
        text = _extract_text(tmp_path, suffix)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"文本提取失败: {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return {
        "status": "ok",
        "filename": file.filename,
        "size": total,
        "text_content": text,
    }


# ── Session CRUD ──

@router.post("/session")
async def create_session(req: CreateSessionRequest, request: Request):
    engine = _get_engine(request)
    session = engine.create_session(req.title, req.source_material, req.config)
    return {
        "id": session.id,
        "title": session.title,
        "status": session.status.value,
        "created_at": session.created_at,
    }


@router.get("/session/{session_id}")
async def get_session(session_id: str, request: Request):
    engine = _get_engine(request)
    session = engine.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return {
        "id": session.id,
        "title": session.title,
        "status": session.status.value,
        "phase": session.phase.value,
        "entity_count": session.entity_count,
        "relation_count": session.relation_count,
        "agent_count": session.agent_count,
        "current_round": session.current_round,
        "total_rounds": session.total_rounds,
        "created_at": session.created_at,
        "error": session.error,
    }


@router.get("/sessions")
async def list_sessions(limit: int = Query(50, ge=1, le=200), request: Request = None):
    engine = _get_engine(request)
    return engine.list_sessions(limit=limit)


@router.delete("/session/{session_id}")
async def delete_session(session_id: str, request: Request):
    engine = _get_engine(request)
    engine.delete_session(session_id)
    return {"deleted": session_id}


# ── Pipeline control ──

@router.post("/session/{session_id}/start")
async def start_deduction(session_id: str, request: Request):
    engine = _get_engine(request)
    session = engine.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    try:
        updated = await engine.start(session_id)
        return {
            "session_id": updated.id,
            "status": updated.status.value,
            "report": {
                "summary": updated.report.summary if updated.report else "",
            } if updated.report else None,
        }
    except Exception as e:
        logger.exception("[Deduction] start failed")
        raise HTTPException(500, str(e))


@router.post("/session/{session_id}/inject")
async def inject_event(session_id: str, req: InjectEventRequest, request: Request):
    engine = _get_engine(request)
    session = engine.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    engine.log(session_id, "inject", f"事件注入: {req.event_description}")
    return {"session_id": session_id, "injected": True}


# ── Data export ──

@router.get("/session/{session_id}/graph")
async def get_graph_data(session_id: str, request: Request):
    engine = _get_engine(request)
    session = engine.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    graph = engine.get_graph(session_id)
    return graph.export_graph_data()


@router.get("/session/{session_id}/report")
async def get_report(session_id: str, request: Request):
    engine = _get_engine(request)
    session = engine.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    data = engine.session_store.get(session_id)
    if data is None:
        raise HTTPException(404, "Session data not found")
    report_json = data.get("report_json", {}) or {}
    return {
        "session_id": session_id,
        "status": session.status.value,
        "report": report_json if isinstance(report_json, dict) else json.loads(report_json),
    }


@router.get("/session/{session_id}/logs")
async def get_logs(session_id: str, limit: int = Query(200), request: Request = None):
    engine = _get_engine(request)
    return engine.get_logs(session_id, limit=limit)


# ── SSE Stream ──

@router.get("/session/{session_id}/stream")
async def stream_deduction(session_id: str, request: Request):
    async def event_generator():
        engine = _get_engine(request)
        last_log_id = 0
        while True:
            logs = engine.get_logs(session_id, limit=50)
            new_logs = [l for l in logs if l.get("id", 0) > last_log_id]
            for log_entry in new_logs:
                last_log_id = max(last_log_id, log_entry.get("id", 0))
                yield f"data: {json.dumps(log_entry, ensure_ascii=False)}\n\n"

            session = engine.get_session(session_id)
            if session and session.status.value in ("complete", "failed", "paused"):
                yield f"data: {json.dumps({'type': 'status', 'status': session.status.value}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _get_engine(request: Request):
    engine = getattr(request.app.state, "deduction_engine", None)
    if engine is None:
        from openakita.config import settings
        from openakita.deduction.engine import DeductionEngine
        engine = DeductionEngine(settings.project_root)
        request.app.state.deduction_engine = engine
    return engine
