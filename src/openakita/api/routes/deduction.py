"""Deduction Engine API routes — REST + SSE streaming."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/deduction", tags=["deduction"])


class CreateSessionRequest(BaseModel):
    title: str = Field(default="", description="会话标题")
    source_material: str = Field(default="", description="种子材料/原文")
    config: dict[str, Any] = Field(default_factory=dict)


class InjectEventRequest(BaseModel):
    event_description: str = Field(default="", description="要注入的事件描述")


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
