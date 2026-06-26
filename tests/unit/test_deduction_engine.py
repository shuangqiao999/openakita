"""Deduction Engine integration tests — model/store/orchestrator without LLM."""
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from openakita.deduction.models import (
    DeductionPhase,
    DeductionSession,
    EntityTypeDef,
    Ontology,
    RelationTypeDef,
    SessionStatus,
)
from openakita.deduction.store import DeductionGraphStore
from openakita.deduction.session_store import SessionStore


@pytest.fixture
def tmp_dir():
    d = Path(tempfile.mkdtemp(prefix="test_deduction_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestModels:

    def test_session_defaults(self):
        s = DeductionSession(title="测试")
        assert s.id
        assert len(s.id) == 12
        assert s.status == SessionStatus.CREATED
        assert s.total_rounds == 10

    def test_ontology_serializable(self):
        o = Ontology(
            entities=[EntityTypeDef("Person", "人物", ["role"])],
            relations=[RelationTypeDef("knows", "认识", "Person", "Person")],
        )
        data = json.dumps({
            "entities": [{"name": e.name, "desc": e.description} for e in o.entities],
            "relations": [{"name": r.name, "from": r.from_type, "to": r.to_type} for r in o.relations],
        })
        parsed = json.loads(data)
        assert len(parsed["entities"]) == 1
        assert len(parsed["relations"]) == 1


class TestGraphStore:

    def test_init_and_schema(self, tmp_dir):
        store = DeductionGraphStore(tmp_dir / "graph")
        assert store._conn is not None
        assert (tmp_dir / "graph").exists()

    def test_upsert_entity_and_query(self, tmp_dir):
        store = DeductionGraphStore(tmp_dir / "graph")
        store.upsert_entity("e1", "张三", "Person", "一个程序员")
        store.upsert_entity("e2", "AcmeCorp", "Organization", "科技公司")

        assert store.count_entities() == 2

        persons = store.get_entities_by_type("Person")
        assert len(persons) == 1
        assert persons[0]["name"] == "张三"

    def test_upsert_relation(self, tmp_dir):
        store = DeductionGraphStore(tmp_dir / "graph")
        store.upsert_entity("e1", "李四", "Person", "")
        store.upsert_entity("e2", "BetaInc", "Organization", "")
        store.upsert_relation("e1", "e2", "works_for", weight=0.8, evidence="原文第3段")

        assert store.count_relations() == 1

    def test_export_graph_data(self, tmp_dir):
        store = DeductionGraphStore(tmp_dir / "graph")
        store.upsert_entity("n1", "王五", "Person", "CEO")
        store.upsert_entity("n2", "GammaCorp", "Organization", "创业公司")
        store.upsert_relation("n1", "n2", "founded_by")

        data = store.export_graph_data()
        assert len(data["nodes"]) == 2
        assert len(data["links"]) == 1
        assert data["nodes"][0]["name"] in ("王五", "GammaCorp")

    def test_chunk_and_mention(self, tmp_dir):
        store = DeductionGraphStore(tmp_dir / "graph")
        store.upsert_chunk("c1", "测试内容", "source.md")
        store.upsert_entity("e1", "赵六", "Person", "")
        store.add_mention("c1", "e1", confidence=0.9)

    def test_agent_node(self, tmp_dir):
        store = DeductionGraphStore(tmp_dir / "graph")
        store.upsert_agent_node("ag1", "AgentX", "性格开朗", "来自南方")
        store.upsert_entity("e1", "AgentX", "Person", "")
        store._conn.execute(
            "MATCH (a:Agent {id: 'ag1'}), (e:Entity {id: 'e1'}) "
            "CREATE (a)-[:PARTICIPATES {role: 'embodies'}]->(e)"
        )

    def test_close(self, tmp_dir):
        store = DeductionGraphStore(tmp_dir / "graph")
        store.upsert_entity("x", "Test", "Concept", "")
        store.close()
        assert store._conn is None


class TestSessionStore:

    def test_create_and_get(self, tmp_dir):
        s = SessionStore(tmp_dir / "sessions.db")
        data = s.create("sid-1", "测试会话", "some source", {"rounds": 5})
        assert data["id"] == "sid-1"
        assert data["title"] == "测试会话"

    def test_list(self, tmp_dir):
        s = SessionStore(tmp_dir / "sessions.db")
        s.create("a", "Session A", "")
        s.create("b", "Session B", "")
        items = s.list_all()
        assert len(items) == 2

    def test_update(self, tmp_dir):
        s = SessionStore(tmp_dir / "sessions.db")
        s.create("upd-1", "Old", "")
        s.update("upd-1", status="simulating", entity_count=42)
        got = s.get("upd-1")
        assert got["status"] == "simulating"
        assert got["entity_count"] == 42

    def test_logs(self, tmp_dir):
        s = SessionStore(tmp_dir / "sessions.db")
        s.create("log-1", "LogTest", "")
        s.append_log("log-1", "ontology", "本体生成完成")
        s.append_log("log-1", "graph", "图谱构建完成")
        logs = s.get_logs("log-1")
        assert len(logs) == 2

    def test_delete(self, tmp_dir):
        s = SessionStore(tmp_dir / "sessions.db")
        s.create("del-1", "", "")
        s.delete("del-1")
        assert s.get("del-1") is None


class TestOntologyModule:

    @pytest.mark.asyncio
    async def test_default_ontology(self):
        from openakita.deduction.ontology import _default_ontology
        o = _default_ontology()
        assert len(o.entities) >= 4
        assert len(o.relations) >= 4

    def test_parse_ontology_json(self):
        from openakita.deduction.ontology import _parse_ontology
        o = _parse_ontology('{"entities": [{"name": "Car", "description": "汽车"}], "relations": []}')
        assert len(o.entities) == 1
        assert o.entities[0].name == "Car"

    def test_parse_bad_json_falls_back(self):
        from openakita.deduction.ontology import _parse_ontology
        o = _parse_ontology("not json at all")
        assert len(o.entities) >= 4  # defaults

    @pytest.mark.skip(reason="LLMClient init triggers circular import in test env")
    @pytest.mark.asyncio
    async def test_ontology_llm_stream(self):
        from openakita.deduction.ontology import generate_ontology
        from unittest.mock import AsyncMock

        with patch("openakita.deduction.ontology.LLMClient", create=True) as mock_cls:
            instance = AsyncMock()
            instance.chat = AsyncMock(return_value="{}")
            mock_cls.return_value = instance
            result = await generate_ontology("test text")
            assert isinstance(result, Ontology)
            assert len(result.entities) >= 4
            mock.return_value.chat = AsyncMock(return_value="{}")
            result = await generate_ontology("test text")
            assert isinstance(result, Ontology)
            assert len(result.entities) >= 4


class TestGraphBuilder:

    def test_chunk_text(self):
        from openakita.knowledge.chunker import TextChunker
        chunker = TextChunker(strategy="paragraph", max_chunk_size=1536)
        text = "段落1内容\n\n段落2内容\n\n段落3内容"
        chunks = chunker.chunk(text)
        assert len(chunks) >= 1

    def test_parse_extraction(self):
        from openakita.deduction.graph_builder import _parse_extraction
        entities, relations = _parse_extraction(
            '[{"entity": "Alice", "type": "Person"}, {"source": "Alice", "target": "Bob", "relation": "knows"}]')
        assert len(entities) == 1
        assert len(relations) == 1


class TestEngine:

    def test_engine_creates_session(self, tmp_dir):
        from openakita.deduction.engine import DeductionEngine
        engine = DeductionEngine(tmp_dir)
        session = engine.create_session("测试", "这是种子材料内容")
        assert session.id
        assert session.title == "测试"
        assert session.status == SessionStatus.CREATED
        engine.close()

    def test_engine_list_sessions(self, tmp_dir):
        from openakita.deduction.engine import DeductionEngine
        engine = DeductionEngine(tmp_dir)
        engine.create_session("A", "content A")
        engine.create_session("B", "content B")
        items = engine.list_sessions()
        assert len(items) == 2
        engine.close()

    def test_engine_delete_session(self, tmp_dir):
        from openakita.deduction.engine import DeductionEngine
        engine = DeductionEngine(tmp_dir)
        sid = engine.create_session("Del", "x").id
        engine.delete_session(sid)
        assert engine.get_session(sid) is None
        engine.close()

    def test_engine_log(self, tmp_dir):
        from openakita.deduction.engine import DeductionEngine
        engine = DeductionEngine(tmp_dir)
        sid = engine.create_session("Log", "y").id
        engine.log(sid, "test", "hello world")
        logs = engine.get_logs(sid)
        assert len(logs) == 1
        assert logs[0]["message"] == "hello world"
        engine.close()


class TestReporter:

    def test_parse_report_json(self):
        from openakita.deduction.reporter import _parse_report_json
        data = _parse_report_json(
            '{"summary": "一切正常", "risk_alerts": ["风险1"], "recommendations": ["建议1"]}')
        assert data["summary"] == "一切正常"
        assert len(data["risk_alerts"]) == 1

    def test_default_report_on_bad_json(self):
        from openakita.deduction.reporter import _parse_report_json
        data = _parse_report_json("not json")
        assert data == {}
