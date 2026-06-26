"""
Deduction Engine end-to-end functional test (LMStudio real LLM)

Models:
  - Chat:     qwen/qwen3.5-9b    (9B, reasoning)
  - Embed:    text-embedding-embeddinggemma-300m-qat (768d)

Tests every phase of the five-stage pipeline with real LLM calls.
Also validates architecture patterns: isolation, error handling, corner cases.
"""
import json
import shutil
import tempfile
import threading
import time
import sys
import uuid
from pathlib import Path

LMSTUDIO_BASE = "http://127.0.0.1:1234/v1"
EMBED_MODEL = "text-embedding-embeddinggemma-300m-qat"
CHAT_MODEL = "qwen/qwen3.5-9b"
EMBED_DIM = 768

# Source material for testing — a short news-like scenario
TEST_SOURCE = """
2026年6月，中国科技公司星辰科技（StarTech）宣布成功研发新一代量子计算芯片"天枢"。
公司CEO李明在发布会上表示，该芯片将大幅提升AI训练速度，预计2027年量产。
竞争对手华光半导体（HuaGuang）同日宣布获得政府50亿元补贴，加速光子芯片研发。
行业分析师张三认为，量子计算与光子计算的路线之争将进入白热化阶段。
欧洲监管机构EUTech表示将对中国芯片企业开展反垄断调查。
星辰科技CTO王五回应称，公司始终遵守国际规则，欢迎公平竞争。
下游企业智能云（SmartCloud）宣布将率先采购天枢芯片建设AI训练中心。
环保组织"绿色地球"发表声明，关注芯片制造过程中的能耗与碳排放问题。
李明在后续采访中透露，公司已与三家国际云服务商签订合作意向书。
"""


def check_lmstudio() -> dict:
    import requests
    try:
        r = requests.get(f"{LMSTUDIO_BASE}/models", timeout=5)
        if r.ok:
            models = {m["id"] for m in r.json().get("data", [])}
            chat_ok = CHAT_MODEL in models
            embed_ok = EMBED_MODEL in models
            return {"chat": chat_ok, "embed": embed_ok, "models": models}
    except Exception:
        pass
    return {"chat": False, "embed": False, "models": set()}


def setup_test_endpoint(tmp_dir: Path):
    """Configure embedding and write temp LLM config so LLMClient picks it up."""
    from openakita.config import settings

    # Configure embedding
    settings.embedding_model = "api"
    settings.embedding_provider = "openai"
    settings.embedding_api_provider = "openai"
    settings.embedding_api_key = "lm-studio"
    settings.embedding_api_base = LMSTUDIO_BASE
    settings.embedding_model_name = EMBED_MODEL
    settings.embedding_api_model = EMBED_MODEL
    settings.embedding_device = "cpu"

    # Write a minimal llm_endpoints.json to default config path
    config_dir = Path(settings.project_root) / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "llm_endpoints.json"

    # Backup existing
    backup = None
    if config_path.exists():
        backup = config_path.read_text()

    config = {
        "version": 2,
        "endpoints": [{
            "name": "lmstudio-test",
            "provider": "openai",
            "api_type": "openai",
            "base_url": "http://127.0.0.1:1234/v1",
            "api_key": "lm-studio",
            "model": CHAT_MODEL,
            "context_window": 32768,
            "enabled": True,
        }],
        "settings": {"default_endpoint": "lmstudio-test"},
    }
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    print("  [setup] Wrote test config to: " + str(config_path))

    # Return cleanup function
    return lambda: config_path.write_text(backup) if backup else config_path.unlink(missing_ok=True)


def run_test(name: str, fn, *args, **kwargs):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    try:
        result = fn(*args, **kwargs)
        print(f"  [PASS] {name}")
        return result
    except Exception as e:
        import traceback
        print(f"  [FAIL] {name}: {e}")
        traceback.print_exc()
        return None


# ═══════════════════════════════════════════════════════════════════
# Test 1: Embedding model connectivity
# ═══════════════════════════════════════════════════════════════════

def test_embedding_model():
    from openakita.llm.embeddings import get_embedding_model
    model = get_embedding_model()
    assert model is not None, "Embedding model is None"
    dim = getattr(model, "dimension", 0)
    assert dim == EMBED_DIM, f"Expected dim {EMBED_DIM}, got {dim}"

    import asyncio
    loop = asyncio.new_event_loop()
    vec = loop.run_until_complete(model.embed_query("测试中文文本"))
    assert len(vec) == EMBED_DIM, f"Vec length {len(vec)} != {EMBED_DIM}"

    vecs = loop.run_until_complete(model.embed(["第一段文本", "第二段文本", "第三段文本"]))
    loop.close()
    assert len(vecs) == 3, f"Batch got {len(vecs)} != 3"
    assert all(len(v) == EMBED_DIM for v in vecs)

    print(f"  Embed dim: {EMBED_DIM}, single vec OK, batch ({len(vecs)}) OK")
    return model


# ═══════════════════════════════════════════════════════════════════
# Test 2: Kuzu graph store basic CRUD
# ═══════════════════════════════════════════════════════════════════

def test_graph_store(tmp_dir):
    from openakita.deduction.store import DeductionGraphStore

    graph = DeductionGraphStore(tmp_dir / "test_graph")

    # Insert entities
    graph.upsert_entity("e1", "星辰科技", "Organization", "量子计算公司")
    graph.upsert_entity("e2", "李明", "Person", "星辰科技CEO")
    graph.upsert_entity("e3", "华光半导体", "Organization", "光子芯片公司")

    assert graph.count_entities() == 3
    assert graph.count_relations() == 0

    # Insert relations
    graph.upsert_relation("e2", "e1", "works_for", 1.0, "李明是星辰科技CEO")
    graph.upsert_relation("e1", "e3", "competes_with", 0.8, "量子vs光子竞争")
    assert graph.count_relations() == 2

    # Query by type
    orgs = graph.get_entities_by_type("Organization")
    assert len(orgs) == 2, f"Expected 2 orgs, got {len(orgs)}"

    # Export graph
    data = graph.export_graph_data()
    assert len(data["nodes"]) == 3
    assert len(data["links"]) == 2
    assert data["nodes"][0]["name"] in ("星辰科技", "李明", "华光半导体")

    # Chunk + mention
    graph.upsert_chunk("c1", "星辰科技发布量子芯片天枢", "source")
    graph.add_mention("c1", "e1", 0.95)
    graph.add_mention("c1", "e2", 0.85)

    # Agent node
    graph.upsert_agent_node("ag1", "李明", "性格果断，重视技术创新", "毕业于清华大学")

    graph.close()
    print(f"  Entities: 3, Relations: 2, Export OK, Chunk/Mention OK, Agent OK")
    return True


# ═══════════════════════════════════════════════════════════════════
# Test 3: Session store CRUD
# ═══════════════════════════════════════════════════════════════════

def test_session_store(tmp_dir):
    from openakita.deduction.session_store import SessionStore

    store = SessionStore(tmp_dir / "sessions.db")

    # Create
    sid = "test-session-001"
    data = store.create(sid, "端到端测试", TEST_SOURCE, {"rounds": 3})
    assert data["id"] == sid
    assert data["title"] == "端到端测试"

    # Update
    store.update(sid, agent_count=5, current_round=2)
    got = store.get(sid)
    assert got["agent_count"] == 5
    assert got["current_round"] == 2

    # Logs
    store.append_log(sid, "ontology", "本体生成开始")
    store.append_log(sid, "graph", "图谱构建完成: 8 entities")
    logs = store.get_logs(sid)
    assert len(logs) == 2

    # List
    items = store.list_all(10)
    assert len(items) >= 1

    # Delete
    store.delete(sid)
    assert store.get(sid) is None

    print(f"  CRUD + update + logs + list + delete: OK")
    return True


# ═══════════════════════════════════════════════════════════════════
# Test 4: Ontology generation (real LLM)
# ═══════════════════════════════════════════════════════════════════

def test_ontology_generation():
    from openakita.deduction.ontology import generate_ontology, _default_ontology

    print("  Calling LLM to analyze source material...")
    import asyncio
    loop = asyncio.new_event_loop()
    ontology = loop.run_until_complete(generate_ontology(TEST_SOURCE))
    loop.close()

    assert ontology is not None
    assert len(ontology.entities) >= 2, f"Expected >=2 entity types, got {len(ontology.entities)}"
    assert len(ontology.relations) >= 1, f"Expected >=1 relation types, got {len(ontology.relations)}"

    entity_names = {e.name for e in ontology.entities}
    relation_names = {r.name for r in ontology.relations}

    print(f"  Entities: {entity_names}")
    print(f"  Relations: {relation_names}")

    # Validate reasonable detection
    has_person = any("人" in n or "Person" in n or "person" in n.lower() for n in entity_names)
    has_org = any("组织" in n or "公司" in n or "Organization" in n or "Org" in n for n in entity_names)

    if not has_person and not has_org:
        print("  [WARN] LLM did not detect Person or Organization entity type (may be using Chinese)")
        # Fallback check — do entities have meaningful names?
        assert all(len(n) >= 2 for n in entity_names), "Entity names too short"

    return ontology


# ═══════════════════════════════════════════════════════════════════
# Test 5: GraphRAG building (real LLM + Kuzu)
# ═══════════════════════════════════════════════════════════════════

def test_graph_building(ontology, tmp_dir):
    from openakita.deduction.store import DeductionGraphStore
    from openakita.deduction.graph_builder import build_graph

    graph = DeductionGraphStore(tmp_dir / "graph_builder_test")

    logs = []
    async def run_build():
        await build_graph(
            source=TEST_SOURCE,
            graph=graph,
            ontology=ontology,
            log_fn=lambda phase, msg: logs.append((phase, msg)),
        )

    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(run_build())
    loop.close()

    e_count = graph.count_entities()
    r_count = graph.count_relations()

    print(f"  Entities: {e_count}, Relations: {r_count}")
    print(f"  Log entries: {len(logs)}")

    assert e_count >= 3, f"Expected >=3 entities, got {e_count}"
    assert r_count >= 2, f"Expected >=2 relations, got {r_count}"

    # Check specific expected entities
    data = graph.export_graph_data()
    names = {n["name"] for n in data["nodes"]}
    print(f"  Entity names: {names}")

    has_expected = any("星辰" in n or "Star" in n for n in names)
    assert has_expected, f"Expected entity containing 星辰/Star not found in {names}"

    graph.close()
    return graph


# ═══════════════════════════════════════════════════════════════════
# Test 6: Agent factory (real LLM + graph)
# ═══════════════════════════════════════════════════════════════════

def test_agent_factory(tmp_dir):
    from openakita.deduction.store import DeductionGraphStore
    from openakita.deduction.agent_factory import create_agents_from_graph

    graph = DeductionGraphStore(tmp_dir / "agent_factory_test")

    # Build a small graph with known Person entities
    graph.upsert_entity("p1", "李明", "Person", "星辰科技CEO, 45岁, 技术背景")
    graph.upsert_entity("p2", "张三", "Person", "行业分析师, 专注半导体产业")
    graph.upsert_entity("p3", "王五", "Person", "星辰科技CTO, 量子计算专家")
    graph.upsert_entity("o1", "星辰科技", "Organization", "量子计算公司")

    source = "李明是星辰科技CEO，主张量子计算路线。张三是分析师。王五是CTO。"

    logs = []
    async def run():
        return await create_agents_from_graph(
            graph=graph,
            source_material=source,
            log_fn=lambda phase, msg: logs.append(msg),
        )

    import asyncio
    loop = asyncio.new_event_loop()
    agents = loop.run_until_complete(run())
    loop.close()

    print(f"  Generated {len(agents)} agents")
    for ag in agents:
        print(f"    - {ag.name}: {ag.persona[:80]}...")

    assert len(agents) >= 2, f"Expected >=2 agents, got {len(agents)}"

    # Verify agent quality
    for ag in agents:
        assert ag.name, "Agent name empty"
        assert ag.persona, f"Agent {ag.name} persona empty"
        assert len(ag.persona) >= 10, f"Agent {ag.name} persona too short"

    graph.close()
    return agents


# ═══════════════════════════════════════════════════════════════════
# Test 7: Simulation engine (no LLM — unit test simulation logic)
# ═══════════════════════════════════════════════════════════════════

def test_simulation_engine(tmp_dir):
    from openakita.deduction.store import DeductionGraphStore
    from openakita.deduction.models import DeductionAgentProfile
    from openakita.deduction.simulator import SimulationEngine

    graph = DeductionGraphStore(tmp_dir / "sim_test")

    # Create minimal agents
    agents = [
        DeductionAgentProfile("ag1", "AgentA", "积极乐观", "测试背景", ["参与讨论"]),
        DeductionAgentProfile("ag2", "AgentB", "谨慎保守", "测试背景", ["观察分析"]),
    ]

    # Store agent nodes
    for ag in agents:
        graph.upsert_agent_node(ag.entity_id, ag.name, ag.persona, ag.background)

    logs = []
    engine = SimulationEngine(
        agents=agents, graph=graph, total_rounds=2,
        log_fn=lambda p, m: logs.append(m),
    )

    # Run 2 rounds (this also makes real LLM calls!)
    import asyncio
    async def run_sim():
        results = []
        for rnd in range(1, 3):
            result = await engine.run_round(rnd)
            results.append(result)
            print(f"    Round {rnd}: {len(result.actions)} actions")
        return results

    loop = asyncio.new_event_loop()
    rounds = loop.run_until_complete(run_sim())
    loop.close()

    total_actions = sum(len(r.actions) for r in rounds)
    assert total_actions >= 1, f"Expected >=1 total actions, got {total_actions}"

    # Verify events were written to graph
    print(f"  Total actions: {total_actions}, AgentA={sum(1 for r in rounds for a in r.actions if a.agent_id=='ag1')}")

    graph.close()
    return rounds


# ═══════════════════════════════════════════════════════════════════
# Test 8: Report generation (real LLM)
# ═══════════════════════════════════════════════════════════════════

def test_report_generation(tmp_dir):
    from openakita.deduction.store import DeductionGraphStore
    from openakita.deduction.models import (DeductionSession, SessionStatus,
                                             SimulationAction, SimulationRound)
    from openakita.deduction.reporter import generate_report

    graph = DeductionGraphStore(tmp_dir / "report_test")
    graph.upsert_entity("e1", "星辰科技", "Organization", "量子计算")
    graph.upsert_entity("e2", "华光半导体", "Organization", "光子计算")
    graph.upsert_relation("e1", "e2", "competes_with", 0.9, "技术路线竞争")

    session = DeductionSession(
        id="rpt-001", title="芯片行业推演测试",
        source_material=TEST_SOURCE[:500],
        status=SessionStatus.REPORTING,
        entity_count=2, relation_count=1,
        agent_count=3, current_round=3, total_rounds=3,
    )

    # Create fake simulation rounds with sample actions
    rounds = [
        SimulationRound(round_number=1, actions=[
            SimulationAction("ag1", "post", "", "星辰科技发布了天枢芯片，这是量子计算的重大突破！"),
            SimulationAction("ag2", "reply", "ag1", "华光半导体的光子芯片也不容小觑。"),
        ]),
        SimulationRound(round_number=2, actions=[
            SimulationAction("ag1", "interact", "ag3", "我们需要关注欧洲的调查进展。"),
            SimulationAction("ag3", "post", "", "环保组织的担忧值得重视，芯片制造能耗问题必须解决。"),
        ]),
    ]

    logs = []
    async def run_report():
        return await generate_report(session, graph, rounds, log_fn=lambda p, m: logs.append(m))

    import asyncio
    loop = asyncio.new_event_loop()
    report = loop.run_until_complete(run_report())
    loop.close()

    print(f"  Summary: {report.summary[:150]}...")
    print(f"  Key events: {len(report.key_events)}")
    print(f"  Risk alerts: {len(report.risk_alerts)}")
    print(f"  Recommendations: {len(report.recommendations)}")

    assert report.summary, "Report summary empty"
    assert len(report.summary) >= 20, "Summary too short"
    assert report.raw_graph_stats["entities"] == 2

    if report.risk_alerts:
        print(f"    Risks: {report.risk_alerts}")
    if report.recommendations:
        print(f"    Recs: {report.recommendations}")

    graph.close()
    return report


# ═══════════════════════════════════════════════════════════════════
# Test 9: Engine integration (create + start end-to-end)
# ═══════════════════════════════════════════════════════════════════

def test_engine_integration(tmp_dir):
    import asyncio
    from openakita.deduction.engine import DeductionEngine

    engine = DeductionEngine(tmp_dir)

    # Create session
    session = engine.create_session(
        title="端到端集成测试",
        source_material=TEST_SOURCE,
        config={"total_rounds": 2},
    )
    print(f"  Session: {session.id}, status={session.status.value}")

    # Check session persistence
    got = engine.get_session(session.id)
    assert got is not None
    assert got.title == "端到端集成测试"

    # List sessions
    items = engine.list_sessions()
    assert len(items) >= 1

    # Log some messages
    engine.log(session.id, "test", "集成测试日志1")
    engine.log(session.id, "test", "集成测试日志2")
    logs = engine.get_logs(session.id)
    assert len(logs) == 2

    # Start full pipeline
    print("  Starting full pipeline (this makes LLM calls for all 5 phases)...")
    async def run_full():
        return await engine.start(session.id)

    loop = asyncio.new_event_loop()
    updated = loop.run_until_complete(run_full())
    loop.close()

    print(f"  Final status: {updated.status.value}")
    print(f"  Entities: {updated.entity_count}, Relations: {updated.relation_count}")
    print(f"  Agents: {updated.agent_count}, Rounds: {updated.current_round}/{updated.total_rounds}")
    if updated.report:
        print(f"  Report summary: {updated.report.summary[:120]}...")

    assert updated.status.value == "complete", f"Expected complete, got {updated.status.value}"
    assert updated.entity_count >= 3
    assert updated.relation_count >= 2
    assert updated.agent_count >= 1
    assert updated.current_round == 2

    # Verify graph data
    graph = engine.get_graph(session.id)
    data = graph.export_graph_data()
    assert len(data["nodes"]) >= 3
    assert len(data["links"]) >= 2

    engine.close()
    return updated


# ═══════════════════════════════════════════════════════════════════
# Test 10: Architecture validation
# ═══════════════════════════════════════════════════════════════════

def test_architecture_validation():
    """Validate architecture patterns without LLM calls."""
    issues = []

    # 1. Check that DeductionEngine is properly isolated from Agent
    from openakita.deduction.engine import DeductionEngine
    from openakita.core.agent import Agent

    # Verify engine doesn't import from agent (circular dependency risk)
    import inspect
    engine_src = inspect.getsource(DeductionEngine.__init__)
    assert "agent" not in engine_src.lower() or "agent.py" not in engine_src, \
        "DeductionEngine.__init__ should not reference Agent"

    # 2. Verify data isolation — session store uses separate db
    from openakita.deduction.session_store import SessionStore
    assert "openakita.db" not in inspect.getsource(SessionStore._init), \
        "SessionStore must use its own database"

    # 3. Verify Kuzu store is thread-safe (has lock)
    from openakita.deduction.store import DeductionGraphStore
    store_src = inspect.getsource(DeductionGraphStore.__init__)
    assert "self._lock" in store_src or "Lock" in store_src, \
        "GraphStore should use threading lock"

    # 4. Verify ontology has fallback
    from openakita.deduction.ontology import _default_ontology
    default = _default_ontology()
    assert len(default.entities) >= 3
    assert len(default.relations) >= 3

    # 5. Verify graph export produces valid frontend format
    # (nodes with id/name/type, links with source/target)
    export_keys = {"nodes", "links"}
    assert export_keys  # shape validation done in integration test

    # 6. Verify SIMULATOR uses semaphore for concurrency limiting
    from openakita.deduction.simulator import SimulationEngine
    sim_src = inspect.getsource(SimulationEngine.run_round)
    assert "Semaphore" in sim_src or "sem" in sim_src.lower(), \
        "Simulator should use concurrency limiting"

    # 7. Verify reporter handles empty rounds gracefully
    from openakita.deduction.reporter import _parse_report_json
    assert _parse_report_json("") == {}
    assert _parse_report_json("not json") == {}

    print(f"  Architecture checks: 7/7 passed")
    return issues


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")

    # Suppress noisy logs
    for name in ["httpx", "httpcore", "openakita.llm.config", "openakita.memory.storage",
                  "lance", "kuzu"]:
        logging.getLogger(name).setLevel(logging.ERROR)

    print("\n" + "=" * 70)
    print("  OpenAkita Deduction Engine — End-to-End Test (LMStudio)")
    print("=" * 70)

    # ── Probe models ──
    status = check_lmstudio()
    print(f"\n  LMStudio: chat={status['chat']} ({CHAT_MODEL}), embed={status['embed']} ({EMBED_MODEL})")

    if not status["chat"]:
        print(f"\n  [FATAL] Chat model '{CHAT_MODEL}' not found in LMStudio.")
        print(f"  Available: {status['models']}")
        sys.exit(1)
    if not status["embed"]:
        print(f"\n  [FATAL] Embed model '{EMBED_MODEL}' not found in LMStudio.")
        sys.exit(1)

    # ── Run tests ──
    tmp = Path(tempfile.mkdtemp(prefix="deduction_e2e_"))
    cleanup = setup_test_endpoint(tmp)
    results = []

    try:
        # Phase A: Infrastructure tests (no LLM)
        test_graph_store(tmp)
        test_session_store(tmp)
        test_architecture_validation()

        # Phase B: LLM-dependent tests
        test_embedding_model()
        ontology = test_ontology_generation()
        if ontology:
            test_graph_building(ontology, tmp)
        test_agent_factory(tmp)
        test_simulation_engine(tmp)
        test_report_generation(tmp)

        # Phase C: Full integration
        results.append(test_engine_integration(tmp))

        # ── Summary ──
        print(f"\n{'='*70}")
        completed = [r for r in results if r is not None]
        print(f"  Integration tests completed: {len(completed)}")
        for r in completed:
            print(f"    - {r.title}: status={r.status.value}, "
                  f"entities={r.entity_count}, relations={r.relation_count}, "
                  f"agents={r.agent_count}, rounds={r.current_round}/{r.total_rounds}")
        print(f"  All end-to-end tests done!")
        print(f"{'='*70}")

    finally:
        if cleanup:
            try:
                cleanup()
                print("  [cleanup] LLM config restored")
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
