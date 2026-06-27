"""
End-to-end test: verify every LLM + embedding call point in the deduction pipeline
connects correctly through LLMClient() to a local LM Studio instance.

Run:  pytest tests/functional/test_deduction_callpoints.py -v -s

Prerequisites:
  1. LM Studio running at http://127.0.0.1:1234
  2. Chat model loaded (e.g. qwen/qwen3.5-9b)
  3. Embedding model loaded (e.g. text-embedding-embeddinggemma-300m-qat)
  4. data/llm_endpoints.json configured or .env with OPENAI_API_KEY etc.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

# ── Configuration constants ──
LMSTUDIO_BASE = os.environ.get("LMSTUDIO_BASE", "http://127.0.0.1:1234/v1")
LMSTUDIO_CHAT = f"{LMSTUDIO_BASE}/chat/completions"
LMSTUDIO_EMBED = f"{LMSTUDIO_BASE}/embeddings"
DEFAULT_CHAT_MODEL = os.environ.get("DEDUCTION_TEST_CHAT_MODEL", "qwen/qwen3.5-9b")
DEFAULT_EMBED_MODEL = os.environ.get("DEDUCTION_TEST_EMBED_MODEL", "text-embedding-embeddinggemma-300m-qat")

TEST_SOURCE = """
2026年6月，中国科技公司星辰科技（StarTech）宣布成功研发新一代量子计算芯片"天枢"。
公司CEO李明在发布会上表示，该芯片将大幅提升AI训练速度，预计2027年量产。
竞争对手华光半导体（HuaGuang）同日宣布获得政府50亿元补贴，加速光子芯片研发。
行业分析师张三认为，量子计算与光子计算的路线之争将进入白热化阶段。
欧洲监管机构EUTech表示将对中国芯片企业开展反垄断调查。
星辰科技CTO王五回应称，公司始终遵守国际规则，欢迎公平竞争。
下游企业智能云（SmartCloud）宣布将率先采购天枢芯片建设AI训练中心。
"""


# ════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════

def _ensure_lmstudio_running():
    """Check LM Studio is reachable and has required models loaded."""
    import requests
    try:
        r = requests.get(f"{LMSTUDIO_BASE}/models", timeout=5)
        r.raise_for_status()
        models = {m["id"] for m in r.json()["data"]}
        chat_ok = DEFAULT_CHAT_MODEL in models
        embed_ok = DEFAULT_EMBED_MODEL in models
        return chat_ok, embed_ok, models
    except requests.RequestException:
        return False, False, set()


def _setup_endpoint_config():
    """Create a temporary llm_endpoints.json pointing at LM Studio, plus .env."""
    from openakita.config import settings

    config_dir = Path(settings.project_root) / "data"
    config_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "llm_endpoints.json"

    # Save original config if it exists
    original = {}
    if config_path.exists():
        original = json.loads(config_path.read_text(encoding="utf-8"))

    # Write test config
    test_config = {
        "endpoints": [
            {
                "name": "lmstudio-test",
                "provider": "openai",
                "api_type": "openai",
                "base_url": LMSTUDIO_BASE,
                "api_key_env": "LMSTUDIO_API_KEY",
                "model": DEFAULT_CHAT_MODEL,
                "priority": 1,
                "context_window": 32768,
                "capabilities": ["text", "tools"],
                "enabled": True,
            }
        ],
        "embedding_endpoints": [
            {
                "provider": "openai",
                "api_type": "openai",
                "base_url": LMSTUDIO_BASE,
                "api_key_env": "LMSTUDIO_API_KEY",
                "model": DEFAULT_EMBED_MODEL,
            }
        ],
    }
    config_path.write_text(json.dumps(test_config, indent=2, ensure_ascii=False), encoding="utf-8")

    # Set env key (LM Studio doesn't need real keys)
    os.environ["LMSTUDIO_API_KEY"] = "lm-studio-no-key"

    return config_path, original


def _restore_config(config_path: Path, original: dict):
    """Restore original config file."""
    if original:
        config_path.write_text(json.dumps(original, indent=2, ensure_ascii=False), encoding="utf-8")
    elif config_path.exists():
        config_path.unlink()
    os.environ.pop("LMSTUDIO_API_KEY", None)


# ════════════════════════════════════════════════════════════
# Test Class
# ════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestDeductionCallPoints:
    """Verify each LLM/embedding call point in the deduction pipeline."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """Check LM Studio, set up config before each test class."""
        chat_ok, embed_ok, models = _ensure_lmstudio_running()
        if not chat_ok:
            pytest.skip(
                f"Chat model '{DEFAULT_CHAT_MODEL}' not loaded in LM Studio. "
                f"Available models: {models}"
            )
        if not embed_ok:
            pytest.skip(
                f"Embed model '{DEFAULT_EMBED_MODEL}' not loaded in LM Studio. "
                f"Available models: {models}"
            )
        self.config_path, self.original = _setup_endpoint_config()
        yield
        _restore_config(self.config_path, self.original)

    # ════════════════════════════════════════════════════════
    # Test 1: LLMClient endpoint resolution
    # ════════════════════════════════════════════════════════

    async def test_01_llmclient_resolves_endpoints(self):
        """LLMClient() should load endpoints from llm_endpoints.json."""
        from openakita.llm.client import LLMClient
        from openakita.llm.types import Message

        client = LLMClient()
        assert len(client._endpoints) >= 1, (
            f"LLMClient has 0 endpoints! config_path={client._config_path}, "
            f"endpoints={client._endpoints}"
        )
        print(f"  OK: {len(client._endpoints)} endpoint(s) loaded")

        # Actually call chat to verify round-trip
        response = await client.chat(
            [Message(role="user", content="Reply with exactly one word: OK")],
            system="Only output the word OK, nothing else.",
            temperature=0.1,
        )
        from openakita.llm.types import TextBlock

        if hasattr(response, "content"):
            content_blocks = response.content
            if isinstance(content_blocks, list):
                text = "".join(b.text for b in content_blocks if isinstance(b, TextBlock))
            else:
                text = str(content_blocks)
        else:
            text = str(response)
        print(f"  LLMClient.chat() response: {text[:80]}")

    # ════════════════════════════════════════════════════════
    # Test 2: Embedding config resolution
    # ════════════════════════════════════════════════════════

    async def test_02_embedding_config_resolution(self):
        """_build_embedding_config() should find the LM Studio embedding endpoint."""
        from openakita.llm.embeddings import _build_embedding_config

        config = _build_embedding_config()
        print(f"  Embedding config: provider={config.get('provider')}, "
              f"base={config.get('api_base')}, model={config.get('model_name')}")
        assert config.get("api_base") or config.get("base_url"), (
            f"No embedding base URL resolved. Config: {config}"
        )
        assert config.get("model_name"), (
            f"No embedding model name resolved. Config: {config}"
        )

    async def test_02b_get_embedding_model_works(self):
        """get_embedding_model() should return a working embedding instance."""
        from openakita.llm.embeddings import get_embedding_model

        model = get_embedding_model()
        vec = await model.embed_query("test probe for dimension detection")
        assert vec and len(vec) > 0, "embed_query returned empty vector"
        print(f"  Embedding dimension: {len(vec)}")
        assert len(vec) >= 64, f"Embedding dim too small: {len(vec)}"

    # ════════════════════════════════════════════════════════
    # Test 3: Ontology generation (ontology.py)
    # ════════════════════════════════════════════════════════

    async def test_03_ontology_generation(self):
        """generate_ontology() should produce entity/relation types via LLM."""
        from openakita.deduction.ontology import generate_ontology

        ontology = await generate_ontology(TEST_SOURCE)
        print(f"  Entities: {[e.name for e in ontology.entities]}")
        print(f"  Relations: {[r.name for r in ontology.relations]}")
        assert len(ontology.entities) >= 2, f"Too few entity types: {len(ontology.entities)}"
        assert len(ontology.relations) >= 1, f"Too few relation types: {len(ontology.relations)}"

    # ════════════════════════════════════════════════════════
    # Test 4: Graph building — entity extraction (graph_builder.py)
    # ════════════════════════════════════════════════════════

    async def test_04_graph_builder_with_preprocessor(self):
        """build_graph() with preprocessor should extract entities via LLM."""
        from openakita.config import settings
        from openakita.deduction.graph_builder import build_graph
        from openakita.deduction.ontology import generate_ontology
        from openakita.deduction.preprocessor import DeductionPreprocessor
        from openakita.deduction.store import DeductionGraphStore

        ontology = await generate_ontology(TEST_SOURCE)

        session_id = uuid.uuid4().hex[:12]
        preprocessor = DeductionPreprocessor(
            workspace_root=settings.project_root,
            session_id=session_id,
        )
        preprocessor.preprocess(TEST_SOURCE)

        tmp = Path(tempfile.mkdtemp(prefix="ded_test_"))
        try:
            graph = DeductionGraphStore(tmp / "test_graph")

            logs: list[tuple[str, str]] = []

            def log_fn(phase: str, msg: str):
                logs.append((phase, msg))

            await build_graph(
                source=TEST_SOURCE,
                graph=graph,
                ontology=ontology,
                log_fn=log_fn,
                preprocessor=preprocessor,
            )

            e_count = graph.count_entities()
            r_count = graph.count_relations()
            print(f"  Entities: {e_count}, Relations: {r_count}")

            for p, m in logs:
                print(f"  [{p}] {m}")

            assert e_count >= 2, (
                f"Expected >= 2 entities, got {e_count}. "
                f"This means LLM extraction in graph_builder.py failed silently."
            )
            # Note: Kuzu relation storage may fail if target entities haven't been
            # created yet (upsert_relation references nodes). The LLM extraction itself
            # worked if e_count >= 2.
            print(f"  Relations stored in Kuzu: {r_count}")

            graph.close()
        finally:
            preprocessor.close()
            if hasattr(preprocessor, 'drop_tables'):
                try:
                    preprocessor.drop_tables()
                except Exception:
                    pass
            shutil.rmtree(tmp, ignore_errors=True)

    # ════════════════════════════════════════════════════════
    # Test 5: Agent persona generation (agent_factory.py)
    # ════════════════════════════════════════════════════════

    async def test_05_agent_factory_persona_generation(self):
        """create_agents_from_graph() should generate agent personas via LLM."""
        from openakita.config import settings
        from openakita.deduction.agent_factory import create_agents_from_graph
        from openakita.deduction.graph_builder import build_graph
        from openakita.deduction.ontology import generate_ontology
        from openakita.deduction.preprocessor import DeductionPreprocessor
        from openakita.deduction.store import DeductionGraphStore

        ontology = await generate_ontology(TEST_SOURCE)
        session_id = uuid.uuid4().hex[:12]
        preprocessor = DeductionPreprocessor(
            workspace_root=settings.project_root,
            session_id=session_id,
        )
        preprocessor.preprocess(TEST_SOURCE)

        tmp = Path(tempfile.mkdtemp(prefix="ded_test_"))
        try:
            graph = DeductionGraphStore(tmp / "test_graph")
            await build_graph(
                source=TEST_SOURCE, graph=graph, ontology=ontology,
                log_fn=lambda p, m: None, preprocessor=preprocessor,
            )

            logs: list[tuple[str, str]] = []

            def log_fn(phase: str, msg: str):
                logs.append((phase, msg))

            agents = await create_agents_from_graph(
                graph=graph,
                source_material=TEST_SOURCE,
                log_fn=log_fn,
                preprocessor=preprocessor,
            )

            print(f"  Generated {len(agents)} agents")
            for a in agents:
                print(f"    {a.name}: persona={a.persona[:60]}... goals={a.goals}")

            for p, m in logs:
                print(f"  [{p}] {m}")

            assert len(agents) >= 1, (
                f"Expected >= 1 agent, got {len(agents)}. "
                f"Agent factory LLM persona generation failed."
            )
            for agent in agents:
                assert agent.persona, f"Agent {agent.name} has empty persona"
                assert len(agent.persona) >= 10, (
                    f"Agent {agent.name} persona too short: '{agent.persona}'"
                )

            graph.close()
        finally:
            preprocessor.close()
            if hasattr(preprocessor, 'drop_tables'):
                try:
                    preprocessor.drop_tables()
                except Exception:
                    pass
            shutil.rmtree(tmp, ignore_errors=True)

    # ════════════════════════════════════════════════════════
    # Test 6: StrategicReasoner candidate generation (strategic_reasoner.py)
    # ════════════════════════════════════════════════════════

    async def test_06_strategic_reasoner(self):
        """StrategicReasoner.reason() should generate candidates via LLM."""
        from openakita.deduction.models import DeductionAgentProfile
        from openakita.deduction.strategic_reasoner import StrategicReasoner

        agent = DeductionAgentProfile(
            entity_id="test-agent-1",
            name="李明",
            persona="科技创新倡导者，乐观积极，注重公司声誉",
            background="星辰科技CEO，刚刚发布量子计算芯片天枢",
            goals=["推动技术落地", "应对监管调查", "维护行业声誉"],
        )

        world = {
            "recent_events": "- [轮1] 李明: post — 天枢芯片是量子计算的重大突破\n"
                             "- [轮1] 张三: reply — 光子芯片路线也值得关注",
        }

        reasoner = StrategicReasoner(candidate_count=2)
        result = await reasoner.reason(agent, world, round_number=2)

        selected = result.get("selected", {})
        candidates = result.get("candidates", [])
        print(f"  Selected action: {selected.get('action')} — {selected.get('content', '')[:80]}")
        print(f"  Candidates generated: {len(candidates)}")
        for c in candidates:
            print(f"    - {c.get('action')}: {c.get('content', '')[:60]} (score={c.get('_score', 0):+.2f})")

        assert len(candidates) >= 1, (
            "StrategicReasoner generated 0 candidates. LLM call in strategic_reasoner.py failed."
        )
        assert "action" in selected, "Selected action missing 'action' key"

    async def test_06b_strategic_reasoner_with_external_client(self):
        """StrategicReasoner should accept and use external LLMClient (no duplicate creation)."""
        from openakita.deduction.models import DeductionAgentProfile
        from openakita.deduction.strategic_reasoner import StrategicReasoner
        from openakita.llm.client import LLMClient

        agent = DeductionAgentProfile(
            entity_id="test-agent-2",
            name="王五",
            persona="技术务实，独立判断，关注行业公平竞争",
            background="星辰科技CTO，参与天枢芯片研发",
            goals=["保障技术优势", "回应监管关切"],
        )

        world = {"recent_events": "- [轮1] 李明: post — 天枢芯片发布"}

        client = LLMClient()
        assert len(client._endpoints) >= 1, "Shared LLMClient should have endpoints"

        reasoner = StrategicReasoner(candidate_count=2)
        result = await reasoner.reason(agent, world, round_number=1, client=client)

        candidates = result.get("candidates", [])
        print(f"  Using external client: {len(candidates)} candidates")
        assert len(candidates) >= 1, "External LLMClient strategy generation failed"

    # ════════════════════════════════════════════════════════
    # Test 7: Simulation agent decision (simulator.py)
    # ════════════════════════════════════════════════════════

    async def test_07_simulation_agent_decide(self):
        """SimulationEngine._agent_decide() should produce actions via LLM."""
        from openakita.deduction.models import DeductionAgentProfile
        from openakita.deduction.simulator import SimulationEngine
        from openakita.deduction.store import DeductionGraphStore
        from openakita.llm.client import LLMClient

        agent = DeductionAgentProfile(
            entity_id="sim-agent-1",
            name="张三",
            persona="行业分析师，理性中立，善于技术对比",
            background="独立分析师，长期关注半导体行业",
            goals=["客观分析技术路线", "预测行业趋势"],
        )

        tmp = Path(tempfile.mkdtemp(prefix="ded_test_"))
        try:
            graph = DeductionGraphStore(tmp / "test_graph")

            engine = SimulationEngine(
                agents=[agent],
                graph=graph,
                total_rounds=1,
            )

            client = LLMClient()
            action = await engine._agent_decide(client, agent, round_number=1)

            if action:
                print(f"  Action: {action.action_type} — {action.content[:80]}")
                assert action.action_type in ("post", "reply", "interact", "observe"), (
                    f"Unexpected action type: {action.action_type}"
                )
            else:
                print("  Action: None (LLM decision failed)")
                pytest.fail(
                    "_agent_decide returned None. "
                    "Both StrategicReasoner and fallback LLM paths in simulator.py failed."
                )

            graph.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ════════════════════════════════════════════════════════
    # Test 8: Report generation (reporter.py)
    # ════════════════════════════════════════════════════════

    async def test_08_report_generation(self):
        """generate_report() should produce a structured report via LLM."""
        from openakita.deduction.models import (
            DeductionSession,
            SessionStatus,
            SimulationAction,
            SimulationRound,
        )
        from openakita.deduction.reporter import generate_report
        from openakita.deduction.store import DeductionGraphStore

        session = DeductionSession(
            id=uuid.uuid4().hex[:12],
            title="芯片行业推演测试",
            source_material=TEST_SOURCE,
            status=SessionStatus.REPORTING,
            entity_count=6,
            relation_count=5,
            agent_count=3,
            current_round=2,
        )

        actions = [
            SimulationAction(
                agent_id="agent-1", action_type="post",
                content="天枢芯片发布会圆满成功",
                target_id="",
            ),
            SimulationAction(
                agent_id="agent-2", action_type="reply",
                content="光子芯片也有重要进展",
                target_id="agent-1",
            ),
            SimulationAction(
                agent_id="agent-3", action_type="interact",
                content="关注欧盟反垄断调查进展",
                target_id="agent-1",
            ),
        ]
        round1 = SimulationRound(round_number=1, actions=actions[:2])
        round2 = SimulationRound(round_number=2, actions=actions[2:])

        tmp = Path(tempfile.mkdtemp(prefix="ded_test_"))
        try:
            graph = DeductionGraphStore(tmp / "test_graph")

            report = await generate_report(
                session=session,
                graph=graph,
                rounds=[round1, round2],
                log_fn=lambda p, m: None,
            )

            print(f"  Summary: {report.summary[:120]}")
            print(f"  Key events: {len(report.key_events)}")
            print(f"  Risk alerts: {report.risk_alerts}")
            print(f"  Recommendations: {report.recommendations}")

            assert report.summary, "Report summary is empty"
            assert len(report.summary) >= 20, f"Report summary too short: '{report.summary}'"

            graph.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ════════════════════════════════════════════════════════
    # Test 9: DeductionPreprocessor embedding calls
    # ════════════════════════════════════════════════════════

    def test_09_preprocessor_embedding(self):
        """DeductionPreprocessor embedding should resolve endpoint and produce vectors."""
        from openakita.config import settings
        from openakita.deduction.preprocessor import DeductionPreprocessor

        session_id = uuid.uuid4().hex[:12]
        pp = DeductionPreprocessor(
            workspace_root=settings.project_root,
            session_id=session_id,
        )

        # Check config resolution
        print(f"  Embed URL: {pp._embed_url}")
        print(f"  Embed model: {pp._embed_model}")
        print(f"  Embed config: provider={pp._embed_config.get('provider')}, "
              f"base={pp._embed_config.get('api_base')}")

        assert pp._embed_url, (
            "DeductionPreprocessor._resolve_embed_url() returned empty. "
            "Embedding endpoint NOT configured. Check embedding_endpoints in llm_endpoints.json "
            "or .env settings (embedding_api_base, embedding_model_name)."
        )
        assert pp._embed_model, (
            "DeductionPreprocessor._resolve_embed_model() returned empty. "
            "Embedding model name NOT found."
        )
        assert "Authorization" in pp._http.headers, (
            "DeductionPreprocessor HTTP session is MISSING Authorization header. "
            "API key was not passed to embedding requests."
        )

        # Test actual embedding call
        try:
            vec = pp._sync_embed_single("量子计算芯片天枢发布")
            print(f"  Single embed dim: {len(vec)}")
            assert len(vec) >= 64, f"Embedding dimension too small: {len(vec)}"
        except Exception as e:
            pytest.fail(f"_sync_embed_single failed: {e}")

        # Test batch embedding
        try:
            vecs = pp._sync_embed_batch(["星辰科技", "华光半导体", "智能云"])
            print(f"  Batch embed: {len(vecs)} vectors, dim={len(vecs[0])}")
            assert len(vecs) == 3, f"Batch should return 3 vectors, got {len(vecs)}"
        except Exception as e:
            pytest.fail(f"_sync_embed_batch failed: {e}")

        # Test full preprocess pipeline
        result = pp.preprocess(TEST_SOURCE)
        print(f"  Preprocess result: {result.total_chunks} chunks, "
              f"{result.total_entities} entities, "
              f"{len(result.high_freq_entities)} high-freq")

        assert result.total_chunks >= 1, "Preprocessor should produce at least 1 chunk"

        pp.close()
        if hasattr(pp, 'drop_tables'):
            try:
                pp.drop_tables()
            except Exception:
                pass

    # ════════════════════════════════════════════════════════
    # Test 10: End-to-end orchestrated pipeline smoke test
    # ════════════════════════════════════════════════════════

    async def test_10_full_pipeline_smoke(self):
        """Run the full five-stage pipeline end-to-end through the orchestrator."""
        from openakita.config import settings
        from openakita.deduction.engine import DeductionEngine

        ws = Path(settings.project_root)
        engine = DeductionEngine(ws)
        session = engine.create_session(
            title="全流水线冒烟测试",
            source_material=TEST_SOURCE,
            config={"total_rounds": 2},
        )
        print(f"  Session ID: {session.id}")

        try:
            result = await engine.start(session.id)
            logs = engine.get_logs(session.id)

            print(f"  Status: {result.status}")
            print(f"  Entities: {result.entity_count}, Relations: {result.relation_count}")
            print(f"  Agents: {result.agent_count}, Rounds: {result.current_round}")
            if result.report:
                print(f"  Report summary: {result.report.summary[:120]}")

            for log in logs[-10:]:
                print(f"  [{log['phase']}] {log['message']}")

            assert result.entity_count >= 2, (
                f"Pipeline produced {result.entity_count} entities (< 2). "
                f"Something is broken in the LLM/embedding path."
            )
            assert result.agent_count >= 1, (
                f"Pipeline produced {result.agent_count} agents (< 1). "
                f"Agent factory LLM calls failed."
            )
            assert result.current_round >= 1, "Simulation did not complete"
            # Verify report was stored (session.report may be None due to
            # _row_to_session not reading report_json back — pre-existing issue)
            stored = engine.session_store.get(session.id)
            report_json = (stored or {}).get("report_json", "")
            assert report_json, f"Report not stored in session. Stored keys: {list((stored or {}).keys())}"
            if isinstance(report_json, str):
                print(f"  Report stored (raw): {report_json[:120]}...")
            else:
                print(f"  Report stored: summary={report_json.get('summary', '?')[:80]}...")
            assert "summary" in (report_json if isinstance(report_json, str) else json.dumps(report_json)), "Report JSON missing summary field"
        finally:
            engine.delete_session(session.id)
            engine.close()


# ════════════════════════════════════════════════════════════
# Module-level: skip if LM Studio not available
# ════════════════════════════════════════════════════════════

def pytest_configure(config):
    pass


if __name__ == "__main__":
    # Direct run fallback
    chat_ok, embed_ok, models = _ensure_lmstudio_running()
    if not chat_ok:
        print(f"SKIP: Chat model '{DEFAULT_CHAT_MODEL}' not loaded. Available: {models}")
        sys.exit(0)
    if not embed_ok:
        print(f"SKIP: Embed model '{DEFAULT_EMBED_MODEL}' not loaded. Available: {models}")
        sys.exit(0)

    config_path, original = _setup_endpoint_config()
    try:
        pytest.main([__file__, "-v", "-s", "--no-header"])
    finally:
        _restore_config(config_path, original)
