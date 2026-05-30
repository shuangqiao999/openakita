"""
Test recall context retrieval — Episode FTS5 + fallback to conversation_turns.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from openakita.memory.retrieval import _parse_days_from_hint
from openakita.memory.storage import MemoryStorage


def build_episode_row(
    ep_id: str,
    session_id: str,
    summary: str,
    goal: str,
    outcome: str = "completed",
    started_at: str | None = None,
    ended_at: str | None = None,
):
    now = datetime.now()
    start = started_at or (now - timedelta(days=2)).isoformat()
    end = ended_at or (now - timedelta(days=2, hours=-1)).isoformat()
    import json

    return {
        "id": ep_id,
        "session_id": session_id,
        "summary": summary,
        "goal": goal,
        "outcome": outcome,
        "started_at": start,
        "ended_at": end,
        "action_nodes": json.dumps([]),
        "entities": json.dumps([]),
        "tools_used": json.dumps([]),
        "linked_memory_ids": json.dumps([]),
        "tags": json.dumps([]),
        "importance_score": 0.5,
        "access_count": 0,
        "source": "session_end",
    }


class TestRecallDaysFromHint:
    def test_empty_returns_default(self):
        assert _parse_days_from_hint("") == 7
        assert _parse_days_from_hint(None) == 7

    def test_today(self):
        assert _parse_days_from_hint("today") == 1

    def test_yesterday(self):
        assert _parse_days_from_hint("yesterday") == 2

    def test_n_days_ago(self):
        assert _parse_days_from_hint("3 days ago") == 3
        assert _parse_days_from_hint("10 days ago") == 10

    def test_n_weeks_ago(self):
        assert _parse_days_from_hint("2 weeks ago") == 14

    def test_this_week(self):
        assert _parse_days_from_hint("this week") == 7

    def test_last_week(self):
        assert _parse_days_from_hint("last week") == 14


class TestRecallContextIntegration:
    """Integration tests using a real MemoryStorage with episodes."""

    def test_count_episodes(self, tmp_path: Path):
        db_path = tmp_path / "test_recall.db"
        storage = MemoryStorage(db_path)
        assert storage.count_episodes() == 0

        # Insert one episode
        ep = build_episode_row("ep-1", "sess-1", "API design discussion", "Design REST API")
        storage.save_episode(ep)
        assert storage.count_episodes() == 1

    def test_fts_search_episodes(self, tmp_path: Path):
        db_path = tmp_path / "test_fts.db"
        storage = MemoryStorage(db_path)

        # Insert episodes
        for i, (sid, summary, goal) in enumerate(
            [
                ("sess-api", "Discussed REST API design patterns", "Design REST API"),
                ("sess-db", "Database schema migration planning", "Plan DB migration"),
                ("sess-ui", "UI component library evaluation", "Evaluate UI library"),
                ("sess-deploy", "Deployment pipeline setup on AWS", "Setup CI/CD"),
            ],
            start=1,
        ):
            ep = build_episode_row(f"ep-{i}", sid, summary, goal)
            storage.save_episode(ep)

        # FTS search for "API"
        results = storage.search_episodes_fts("API", days_back=7, limit=5)
        assert len(results) >= 1
        assert any("API" in r.get("summary", "") for r in results)

        # FTS search for "database"
        results = storage.search_episodes_fts("database", days_back=7, limit=5)
        assert len(results) >= 1

        # FTS search for non-matching term
        results = storage.search_episodes_fts("zzzzz_nonexistent", days_back=7, limit=5)
        assert len(results) == 0

    def test_fts_search_respects_time_window(self, tmp_path: Path):
        db_path = tmp_path / "test_fts_time.db"
        storage = MemoryStorage(db_path)

        now = datetime.now()
        # Recent episode (2 days ago)
        ep1 = build_episode_row(
            "ep-1", "sess-1", "Recent API talk",
            goal="API chat",
            started_at=(now - timedelta(days=2)).isoformat(),
            ended_at=(now - timedelta(days=2, hours=-1)).isoformat(),
        )
        # Old episode (10 days ago)
        ep2 = build_episode_row(
            "ep-2", "sess-2", "Old API talk",
            goal="Old API chat",
            started_at=(now - timedelta(days=10)).isoformat(),
            ended_at=(now - timedelta(days=10, hours=-1)).isoformat(),
        )
        storage.save_episode(ep1)
        storage.save_episode(ep2)

        # Within 7 days — only recent
        results = storage.search_episodes_fts("API", days_back=7, limit=5)
        assert len(results) >= 1
        summaries = [r["summary"] for r in results]
        assert "Recent API talk" in summaries
        assert "Old API talk" not in summaries

        # Within 30 days — both
        results = storage.search_episodes_fts("API", days_back=30, limit=10)
        summaries = [r["summary"] for r in results]
        assert "Recent API talk" in summaries
        assert "Old API talk" in summaries

    def test_recall_fallback_when_no_episodes(self, tmp_path: Path):
        """Empty episodes table should return empty string for fallback."""
        from openakita.memory.retrieval import RetrievalEngine
        from openakita.memory.unified_store import UnifiedStore

        db_path = tmp_path / "test_recall_fb.db"
        store = UnifiedStore(db_path)

        engine = RetrievalEngine(store)
        result = engine.retrieve_recall_context(query="anything")
        # Empty db -> fallback returns ""
        assert result == "" or "[最近" in result

    def test_format_output_structure(self, tmp_path: Path):
        """Verify output format contains expected sections."""
        from openakita.memory.retrieval import RetrievalEngine

        db_path = tmp_path / "test_recall_fmt.db"
        storage = MemoryStorage(db_path)

        now = datetime.now()
        ep = build_episode_row(
            "ep-1", "sess-auth", "OAuth2 login flow implementation",
            goal="Implement OAuth2 login",
            outcome="success",
            started_at=(now - timedelta(days=1)).isoformat(),
            ended_at=(now - timedelta(days=1, hours=2)).isoformat(),
        )
        storage.save_episode(ep)

        from openakita.memory.unified_store import UnifiedStore
        store = UnifiedStore(db_path)

        engine = RetrievalEngine(store)
        result = engine.retrieve_recall_context(query="OAuth", time_hint="7")

        assert result
        assert "近期话题回顾" in result
        assert "OAuth2 login flow" in result
        assert "search_memory" in result or "search_conversation_traces" in result
