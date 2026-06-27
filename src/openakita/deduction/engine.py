"""Deduction Engine — main entry point.

Wires together: session store, Kuzu graph store, orchestrator.
Created once per Agent (like KnowledgeBaseManager).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from openakita.config import settings

from .models import DeductionSession, SessionStatus
from .orchestrator import DeductionOrchestrator
from .session_store import SessionStore
from .store import DeductionGraphStore

logger = logging.getLogger(__name__)


class DeductionEngine:

    def __init__(self, workspace_root: str | Path) -> None:
        ws = Path(workspace_root)
        self._data_dir = ws / settings.deduction_data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self.session_store = SessionStore(self._data_dir / "sessions.db")
        self.graph: DeductionGraphStore | None = None

    def get_graph(self, session_id: str) -> DeductionGraphStore:
        if self.graph is not None:
            return self.graph
        path = self._data_dir / "graphs" / session_id / "kuzu"
        self.graph = DeductionGraphStore(path)
        return self.graph

    def close_graph(self) -> None:
        if self.graph is not None:
            self.graph.close()
            self.graph = None

    def create_session(self, title: str, source_material: str,
                       config: dict[str, Any] | None = None) -> DeductionSession:
        import uuid
        sid = uuid.uuid4().hex[:12]
        data = self.session_store.create(sid, title, source_material, config)
        return self._row_to_session(data)

    def get_session(self, session_id: str) -> DeductionSession | None:
        data = self.session_store.get(session_id)
        if data is None:
            return None
        return self._row_to_session(data)

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.session_store.list_all(limit=limit)

    def delete_session(self, session_id: str) -> None:
        self.close_graph()
        self.session_store.delete(session_id)
        # 清理 LanceDB 向量表 (物理回收磁盘空间)
        try:
            from .preprocessor import DeductionPreprocessor
            pp = DeductionPreprocessor(self._data_dir.parent.parent, session_id)
            pp.drop_tables()
        except Exception as e:
            logger.warning("[Engine] Failed to clean LanceDB for %s: %s", session_id, e)
        # 清理 Kuzu 物理文件夹
        import shutil
        kuzu_path = self._data_dir / "graphs" / session_id
        if kuzu_path.exists():
            shutil.rmtree(kuzu_path, ignore_errors=True)
            logger.info("[Engine] Removed Kuzu graph dir: %s", kuzu_path)

    def log(self, session_id: str, phase: str, message: str) -> None:
        self.session_store.append_log(session_id, phase, message)

    def get_logs(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return self.session_store.get_logs(session_id, limit=limit)

    async def start(self, session_id: str) -> DeductionSession:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")
        if session.status in (SessionStatus.SIMULATING, SessionStatus.REPORTING):
            return session

        graph = self.get_graph(session_id)
        orchestrator = DeductionOrchestrator(
            session=session,
            graph=graph,
            session_store=self.session_store,
            logger_fn=lambda phase, msg: self.log(session_id, phase, msg),
        )

        await orchestrator.run()

        updated = self.get_session(session_id)
        if updated is None:
            raise RuntimeError("Session lost after pipeline")
        return updated

    @staticmethod
    def _row_to_session(data: dict[str, Any]) -> DeductionSession:
        config = data.get("config_json", {}) or {}
        return DeductionSession(
            id=data["id"],
            title=data.get("title", ""),
            source_material=data.get("source_material", ""),
            status=SessionStatus(data.get("status", "created")),
            entity_count=data.get("entity_count", 0),
            relation_count=data.get("relation_count", 0),
            agent_count=data.get("agent_count", 0),
            current_round=data.get("current_round", 0),
            total_rounds=config.get("total_rounds", data.get("total_rounds", 10)),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            error=data.get("error", ""),
        )

    def close(self) -> None:
        self.close_graph()
