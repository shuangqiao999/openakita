"""
P0-P3 Hardening Verification Test (LMStudio real LLM + embedding)

Tests ALL 4 fixes:
  P0: store.py — $param MERGE + single-quote sanitized SET (Kuzu 0.11.3 safe)
  P1: engine.delete_session — LanceDB table drop + Kuzu dir rmtree
  P2: reasoner + agent_factory — asyncio.to_thread wrapping
  P3: preprocessor — embedding model name from settings
"""
import json, time, sys, shutil, tempfile, requests, re
from pathlib import Path

LMSTUDIO_CHAT  = "http://127.0.0.1:1234/v1/chat/completions"
LMSTUDIO_EMBED = "http://127.0.0.1:1234/v1/embeddings"
CHAT_MODEL     = "qwen/qwen3.5-9b"
EMBED_MODEL    = "text-embedding-embeddinggemma-300m-qat"

PASS, FAIL = 0, 0
def chk(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  [PASS] {name} {detail}")
    else:    FAIL += 1; print(f"  [FAIL] {name} {detail}")

def chat(prompt, system="", temperature=0.3, max_tokens=512):
    msgs = []
    if system: msgs.append({"role":"system","content":system})
    msgs.append({"role":"user","content":prompt})
    r = requests.post(LMSTUDIO_CHAT, json={
        "model":CHAT_MODEL,"messages":msgs,"temperature":temperature,"max_tokens":max_tokens
    }, timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def setup_config():
    from openakita.config import settings
    settings.embedding_model = "api"
    settings.embedding_provider = "openai"
    settings.embedding_api_provider = "openai"
    settings.embedding_api_key = "lm-studio"
    settings.embedding_api_base = "http://127.0.0.1:1234/v1"
    settings.embedding_model_name = EMBED_MODEL
    settings.embedding_api_model = EMBED_MODEL
    settings.embedding_device = "cpu"
    p = Path(settings.project_root) / "data" / "llm_endpoints.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version":2, "endpoints":[{
        "name":"lmstudio","provider":"openai","api_type":"openai",
        "base_url":"http://127.0.0.1:1234/v1","api_key":"lm-studio",
        "model":CHAT_MODEL,"context_window":32768,"enabled":True
    }],"settings":{"default_endpoint":"lmstudio"}}, indent=2))


# ════════════════════════════════════════════════
print("="*60)
print("  P0-P3 Hardening Verification Test")
print("="*60)

# Probe models
r = requests.get("http://127.0.0.1:1234/v1/models", timeout=5)
models = {m["id"] for m in r.json()["data"]}
if CHAT_MODEL not in models or EMBED_MODEL not in models:
    print("SKIP: models not loaded"); sys.exit(0)
setup_config()
sf = lambda msgs, sys, t: chat(msgs[0]["content"], system=sys, temperature=t, max_tokens=512)

# ════════════════════════════════════════════════
print("\n  === P0: store.py parameterized queries ===")
# ════════════════════════════════════════════════
from openakita.deduction.store import DeductionGraphStore
tmp_p0 = Path(tempfile.mkdtemp(prefix="p0_"))
try:
    g = DeductionGraphStore(tmp_p0 / "kuzu")
    # Test with special characters that would break unescaped SQL
    g.upsert_entity("e1", "O'Brien & Co", "Organization", "A 'test' entity")
    g.upsert_entity("e2", "Test", "Person", "Normal")
    chk("upsert with special chars", g.count_entities() == 2)
    data = g.export_graph_data()
    names = {n["name"] for n in data["nodes"]}
    chk("special char name preserved", "O'Brien & Co" in names)
    g.close()
finally:
    shutil.rmtree(tmp_p0, ignore_errors=True)

# ════════════════════════════════════════════════
print("\n  === P1: engine.delete_session cleanup ===")
# ════════════════════════════════════════════════
from openakita.deduction.preprocessor import DeductionPreprocessor
from openakita.deduction.engine import DeductionEngine
tmp_p1 = Path(tempfile.mkdtemp(prefix="p1_"))
try:
    engine = DeductionEngine(tmp_p1)
    sid = engine.create_session("Cleanup Test", "sample text").id
    # Preprocess to create LanceDB tables
    pp = DeductionPreprocessor(tmp_p1, sid)
    pp.preprocess("test source material for cleanup verification")
    # Verify tables exist
    import lancedb
    db = lancedb.connect(str(tmp_p1 / "data" / "lancedb"))
    tables_before = set(db.table_names())
    chk("LanceDB tables exist before delete", len(tables_before) >= 1,
        f"found {len(tables_before)} tables: {tables_before}")
    # Delete session
    engine.delete_session(sid)
    tables_after = set(db.table_names())
    ded_tables = {t for t in tables_after if sid in t}
    chk("LanceDB tables cleaned after delete",
        len(ded_tables) == 0,
        f"remaining session tables: {ded_tables}")
    engine.close()
finally:
    shutil.rmtree(tmp_p1, ignore_errors=True)

# ════════════════════════════════════════════════
print("\n  === P2: asyncio.to_thread wrapping ===")
# ════════════════════════════════════════════════
import asyncio, inspect
from openakita.deduction.strategic_reasoner import StrategicReasoner
reasoner_src = inspect.getsource(StrategicReasoner.__init__)
chk("reasoner has _chat_fn attr", "_chat_fn" in reasoner_src)
reason_src = inspect.getsource(StrategicReasoner.reason)
chk("reason() uses asyncio.to_thread for chat_fn",
    "asyncio.to_thread" in reason_src)

from openakita.deduction.agent_factory import create_agents_from_graph
af_src = inspect.getsource(create_agents_from_graph)
chk("agent_factory uses asyncio.to_thread for chat_fn",
    "asyncio.to_thread" in af_src)

# ════════════════════════════════════════════════
print("\n  === P3: embedding model from settings ===")
# ════════════════════════════════════════════════
tmp_p3 = Path(tempfile.mkdtemp(prefix="p3_"))
try:
    pp3 = DeductionPreprocessor(tmp_p3, "p3-test")
    model = getattr(pp3, "_embed_model", "")
    chk("embed model not empty", model != "", f"model='{model}'")
    chk("embed model matches settings", model == EMBED_MODEL,
        f"got '{model}' expected '{EMBED_MODEL}'")
    pp3.close()
finally:
    shutil.rmtree(tmp_p3, ignore_errors=True)

# ════════════════════════════════════════════════
print("\n  === Integration: Full pipeline with all fixes ===")
# ════════════════════════════════════════════════
tmp_int = Path(tempfile.mkdtemp(prefix="int_"))
try:
    from openakita.deduction.models import DeductionAgentProfile
    from openakita.deduction.simulator import SimulationEngine

    pp_int = DeductionPreprocessor(tmp_int, "integration")
    result = pp_int.preprocess("AI company NeuraTech releases AGI. Competitor SafeAI objects.")
    chk("integration: preprocessor OK", result is not None and pp_int._embed_model != "")

    g_int = DeductionGraphStore(tmp_int / "kuzu")
    g_int.upsert_entity("e1", "Alice", "Person", "NeuraTech CEO")
    g_int.upsert_entity("e2", "Bob", "Person", "SafeAI CEO")

    # Test agent_factory with chat_fn (async to_thread)
    loop = asyncio.new_event_loop()
    agents = loop.run_until_complete(
        create_agents_from_graph(g_int, "test source", lambda *a: None, chat_fn=sf))
    loop.close()
    chk("integration: agents created", len(agents) >= 1)

    # Test reasoner with chat_fn (async to_thread)
    reasoner = StrategicReasoner(preprocessor=pp_int, chat_fn=sf)
    loop2 = asyncio.new_event_loop()
    decision = loop2.run_until_complete(
        reasoner.reason(agents[0], {"recent_events": "Round 1"}, 1))
    loop2.close()
    chk("integration: reasoner with chat_fn OK",
        len(decision.get("candidates", [])) >= 1)

    # Test engine delete_session cleanup
    engine_int = DeductionEngine(tmp_int)
    sid_int = engine_int.create_session("Integration", "test").id
    # Create LanceDB tables for this session
    pp2 = DeductionPreprocessor(tmp_int, sid_int)
    pp2.preprocess("integration test source")
    import lancedb as lb
    db_int = lb.connect(str(tmp_int / "data" / "lancedb"))
    before = set(db_int.table_names())
    engine_int.delete_session(sid_int)
    after = set(db_int.table_names())
    ded_tables = {t for t in after if sid_int in t}
    chk("integration: delete cleans LanceDB",
        len(ded_tables) == 0,
        f"remaining: {ded_tables}")
    engine_int.close()
    g_int.close()
finally:
    shutil.rmtree(tmp_int, ignore_errors=True)

print(f"\n{'='*60}")
print(f"  RESULTS: {PASS}/{PASS+FAIL} passed, {FAIL} failed")
print(f"{'='*60}")
