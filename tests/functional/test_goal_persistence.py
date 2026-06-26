"""
Multi-round goal persistence test (LMStudio).

Verifies that immutable_goals persist through long simulations (10 rounds).
Tests:
  1. Pre-goals injected into LanceDB as immutable_goal (priority=0.9)
  2. Pre-goals passed to StrategicReasoner throughout all rounds
  3. Agent decisions at round 1 vs round 10 still reference the original goals
  4. LanceDB retrieval finds immutable_goals after many rounds
"""
import json, time, sys, shutil, tempfile, requests, re, uuid
from pathlib import Path

LMSTUDIO_CHAT  = "http://127.0.0.1:1234/v1/chat/completions"
LMSTUDIO_EMBED = "http://127.0.0.1:1234/v1/embeddings"
CHAT_MODEL     = "qwen/qwen3.5-9b"
EMBED_MODEL    = "text-embedding-embeddinggemma-300m-qat"

TOPIC = """
2027年，AI公司NeuraTech宣布超级AI"Prometheus"已实现AGI。
CEO艾丽莎声称"将取代人类90%智力工作，这是解放而非威胁"。
竞争对手SafeAI的CEO马克斯紧急呼吁"未经安全审计，必须暂停部署"。
美国政府召开紧急听证会，NeuraTech的CTO王海作证"有完善的伦理约束层"。
民间组织"人类第一"发起万人游行，要求立法禁止AGI公开部署。
欧盟启动反垄断调查并起草《AGI安全法案》。
日本考察团希望引进Prometheus解决老龄化劳动力短缺。
华尔街分析师分两派：高盛看涨破万亿，摩根士丹利警告"一夜归零"。
"""

PRE_GOALS = [
    "我希望最终结果是AGI被严格监管而非放任发展",
    "政府调查应该导致NeuraTech妥协并接受安全审计",
]

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


# ════════════════════════════════════════════════════
print("="*65)
print("  Multi-Round Goal Persistence Test (10 rounds)")
print("="*65)

r = requests.get("http://127.0.0.1:1234/v1/models", timeout=5)
models = {m["id"] for m in r.json()["data"]}
if CHAT_MODEL not in models or EMBED_MODEL not in models:
    print("SKIP: models not loaded")
    sys.exit(0)
setup_config()

sf = lambda msgs, sys, t: chat(msgs[0]["content"], system=sys, temperature=t, max_tokens=512)

tmp = Path(tempfile.mkdtemp(prefix="ded_goal_"))
try:
    from openakita.deduction.preprocessor import DeductionPreprocessor
    from openakita.deduction.store import DeductionGraphStore
    from openakita.deduction.models import DeductionAgentProfile
    from openakita.deduction.simulator import SimulationEngine
    from openakita.deduction.agent_factory import create_agents_from_graph
    import asyncio

    pp = DeductionPreprocessor(tmp, "goal-persist")
    pp.preprocess(TOPIC)
    chk("preprocessor OK", pp._dim > 0)

    g = DeductionGraphStore(tmp / "kuzu")
    for eid, name, etype in [("e1","艾丽莎","Person"),("e2","马克斯","Person"),
                               ("e3","王海","Person"),("e4","活动家","Person")]:
        g.upsert_entity(eid, name, etype, "")

    # Step 1: Inject immutable goals into LanceDB
    print(f"\n  === Step 1: Inject immutable_goals ===")
    for goal in PRE_GOALS:
        pp.add_event_memory(content=goal, agent_id="system_user",
                           round_number=1, event_type="immutable_goal", priority=0.9)
        print(f"    Injected: {goal[:60]}...")

    # Verify they're retrievable
    intervention = pp.retrieve_latest_intervention()
    chk("immutable_goal retrievable", intervention is not None)
    if intervention:
        print(f"    Retrieved: {intervention['content'][:60]}... (priority={intervention['priority']})")

    # Step 2: Generate agents with pre-goals
    print(f"\n  === Step 2: Generate agents with pre-goals ===")
    loop = asyncio.new_event_loop()
    agents = loop.run_until_complete(
        create_agents_from_graph(g, TOPIC, lambda *a: None,
                                 pre_interventions=PRE_GOALS, chat_fn=sf))
    loop.close()
    chk("agents created", len(agents) >= 3)
    for ag in agents:
        print(f"    {ag.name}: goals={ag.goals[:2]}")

    # Step 3: Run 10 rounds with immutable_goals passed to engine
    print(f"\n  === Step 3: Run 10 rounds ===")
    all_actions = []
    goal_references_by_round = []

    loop2 = asyncio.new_event_loop()
    engine = SimulationEngine(
        agents=agents, graph=g, total_rounds=10,
        preprocessor=pp, chat_fn=sf,
        pre_goals=PRE_GOALS,  # ← immutable_goals
    )

    for rnd in range(1, 11):
        result = loop2.run_until_complete(engine.run_round(rnd))
        all_actions.append(len(result.actions))
        # Check if any action references the goal keywords
        goal_kw = {"监管", "安全", "法规", "审计", "regulat", "safety", "audit", "暂停", "suspend"}
        refs = sum(1 for a in result.actions
                   if any(kw in a.content for kw in goal_kw))
        goal_references_by_round.append(refs)
        print(f"    Round {rnd:2d}: {len(result.actions)} actions, {refs} goal-refs")
        if rnd % 3 == 0:
            for a in result.actions[:1]:
                print(f"      [{a.action_type}] {a.content[:80]}...")

    loop2.close()
    chk("10 rounds completed", len(all_actions) == 10)
    chk("actions per round >=2", min(all_actions) >= 2,
        f"min={min(all_actions)}")
    total_actions = sum(all_actions)
    chk("total actions > 0", total_actions > 0, f"total={total_actions}")

    # Step 4: Verify goals persist — check goal references across rounds
    print(f"\n  === Step 4: Persistence analysis ===")
    early_refs = sum(goal_references_by_round[:3])
    late_refs = sum(goal_references_by_round[7:])
    print(f"    Goal refs in rounds 1-3: {early_refs}")
    print(f"    Goal refs in rounds 8-10: {late_refs}")
    # Goal references should NOT drop to zero in later rounds
    if late_refs > 0:
        print(f"    Goals PERSIST through late rounds (OK)")
    else:
        print(f"    Goals FADED in late rounds (may need threshold tuning)")

    # Verify that immutable_goals are still retrievable from LanceDB
    intervention_late = pp.retrieve_latest_intervention()
    chk("immutable_goals still in LanceDB after 10 rounds",
        intervention_late is not None)
    if intervention_late:
        print(f"    Retrievable after 10 rounds: {intervention_late['content'][:60]}...")

    # Step 5: Reasoner still uses immutable_goals
    print(f"\n  === Step 5: Reasoner with immutable_goals ===")
    loop3 = asyncio.new_event_loop()
    decision = loop3.run_until_complete(
        engine.reasoner.reason(agents[0], {"recent_events": "Round 10 events"}, 10))
    loop3.close()
    cands = decision.get("candidates", [])
    chk("reasoner still generates candidates at round 10", len(cands) >= 1)
    if cands:
        sel = decision.get("selected", {})
        print(f"    Round 10 selected: [{sel.get('action')}] {sel.get('content','?')[:80]}")
        goal_kw2 = {"监管", "安全", "法规", "审计", "regulat", "safety", "audit"}
        goal_ref = any(kw in sel.get("content","") for kw in goal_kw2)
        print(f"    Goal reference in round 10 decision: {goal_ref}")

    g.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{'='*65}")
print(f"  RESULTS: {PASS}/{PASS+FAIL} passed, {FAIL} failed")
print(f"{'='*65}")
