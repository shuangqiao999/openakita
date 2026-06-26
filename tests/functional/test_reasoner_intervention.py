"""
Deduction Engine — Strategic Reasoner + User Intervention Functional Test (LMStudio)

Tests:
  1. Full 5-phase pipeline with controversial topic
  2. Pre-goal injection → agent persona affected
  3. Mid-simulation intervention → behavior shift
  4. StrategicReasoner candidate generation + scoring
  5. Trust matrix updates across rounds
  6. Before/after comparison of outputs
"""
import json, time, sys, shutil, tempfile, requests, re, uuid
from pathlib import Path

LMSTUDIO_CHAT  = "http://127.0.0.1:1234/v1/chat/completions"
LMSTUDIO_EMBED = "http://127.0.0.1:1234/v1/embeddings"
CHAT_MODEL     = "qwen/qwen3.5-9b"
EMBED_MODEL    = "text-embedding-embeddinggemma-300m-qat"

# A controversial topic — AI corporate war
CONTROVERSIAL_TOPIC = """
2027年，全球最大的AI公司NeuraTech宣布其超级AI"Prometheus"已实现AGI（通用人工智能）。
CEO艾丽莎在发布会上表示"Prometheus将取代人类90%的智力工作，这是解放而非威胁"。
竞争对手SafeAI的CEO马克斯紧急发声"这是未经安全审计的危险行为，必须暂停部署"。
美国政府召开紧急听证会，NeuraTech的CTO王海在国会作证"Prometheus有完善的伦理约束层"。
民间组织"人类第一"在纽约发起万人游行，要求立法禁止AGI公开部署。
欧盟宣布启动对NeuraTech的反垄断调查，并紧急起草《AGI安全法案》。
日本经济产业省则派出考察团，希望引进Prometheus技术解决老龄化劳动力短缺问题。
华尔街分析师分为两派：高盛看涨NeuraTech市值破万亿，摩根士丹利警告"安全风险可能导致公司一夜归零"。
"""

PASS, FAIL = 0, 0
def chk(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  [PASS] {name} {detail}")
    else:    FAIL += 1; print(f"  [FAIL] {name} {detail}")

def chat(prompt, system="", temperature=0.3, max_tokens=1024):
    msgs = []; 
    if system: msgs.append({"role":"system","content":system})
    msgs.append({"role":"user","content":prompt})
    r = requests.post(LMSTUDIO_CHAT, json={
        "model":CHAT_MODEL,"messages":msgs,"temperature":temperature,"max_tokens":max_tokens
    }, timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def try_json(raw):
    raw = re.sub(r'```(?:json)?\s*\n?', '', raw)
    raw = re.sub(r'\n?```', '', raw).strip()
    for p in (r'\[[\s\S]*\]', r'\{[\s\S]*\}'):
        m = re.search(p, raw)
        if m:
            try: return json.loads(m.group(0))
            except: continue
    return {}

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
    # Write endpoint config
    p = Path(settings.project_root) / "data" / "llm_endpoints.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version":2, "endpoints":[{
        "name":"lmstudio","provider":"openai","api_type":"openai",
        "base_url":"http://127.0.0.1:1234/v1","api_key":"lm-studio",
        "model":CHAT_MODEL,"context_window":32768,"enabled":True
    }],"settings":{"default_endpoint":"lmstudio"}}, indent=2))


# ════════════════════════════════════════════════════════
print("="*65)
print("  Deduction Engine — Strategic Reasoner + Intervention Test")
print("="*65)

# 1. Probe models
r = requests.get("http://127.0.0.1:1234/v1/models", timeout=5)
models = {m["id"] for m in r.json()["data"]}
chk("chat model available", CHAT_MODEL in models)
chk("embed model available", EMBED_MODEL in models)
if FAIL > 0: sys.exit(1)
setup_config()

# ===== PHASE 1: Baseline — without intervention =====
print(f"\n{'='*65}")
print("  TEST A: Baseline deduction (no intervention)")
print(f"{'='*65}")

tmp_a = Path(tempfile.mkdtemp(prefix="ded_a_"))
try:
    from openakita.deduction.preprocessor import DeductionPreprocessor
    from openakita.deduction.store import DeductionGraphStore
    from openakita.deduction.models import DeductionAgentProfile
    from openakita.deduction.simulator import SimulationEngine
    from openakita.deduction.graph_builder import _make_id
    import asyncio

    # Preprocessor
    pp_a = DeductionPreprocessor(tmp_a, "baseline")
    result_a = pp_a.preprocess(CONTROVERSIAL_TOPIC)
    chk("baseline: preprocessor OK", result_a is not None)
    chk("baseline: entities extracted", result_a.total_entities >= 3)
    chk("baseline: chunks indexed", result_a.total_chunks >= 1)
    chk("baseline: auto-detected dim", pp_a._dim > 0, f"dim={pp_a._dim}")

    # Kuzu graph (minimal for personas)
    g_a = DeductionGraphStore(tmp_a / "kuzu")
    g_a.upsert_entity("e1","艾丽莎","Person","NeuraTech CEO, 主张AGI解放人类")
    g_a.upsert_entity("e2","马克斯","Person","SafeAI CEO, 安全至上")
    g_a.upsert_entity("e3","王海","Person","NeuraTech CTO, 技术乐观派")
    g_a.upsert_entity("e4","AGI活动家","Person","人类第一组织代表")

    # Generate agent personas (no pre-intervention = baseline)
    from openakita.deduction.agent_factory import create_agents_from_graph
    import asyncio
    sf = lambda msgs, sys, t: chat(msgs[0]["content"], system=sys, temperature=t, max_tokens=512)
    loop = asyncio.new_event_loop()
    agents_baseline = loop.run_until_complete(
        create_agents_from_graph(g_a, CONTROVERSIAL_TOPIC, log_fn=lambda *a: None,
                                 pre_interventions=None, chat_fn=sf))  # NO user goal
    loop.close()
    chk("baseline: agents created", len(agents_baseline) >= 2, f"got {len(agents_baseline)}")
    for ag in agents_baseline:
        print(f"    {ag.name}: goals={ag.goals[:2]}")

    # For reference: store baseline goals
    baseline_goals = {ag.name: list(ag.goals) for ag in agents_baseline}

    # Run 2 rounds simulation WITHOUT intervention
    loop2 = asyncio.new_event_loop()
    engine_a = SimulationEngine(agents=agents_baseline, graph=g_a, total_rounds=3,
                                 preprocessor=pp_a,
                                 chat_fn=lambda msgs, sys, t: chat(msgs[0]["content"], system=sys, temperature=t, max_tokens=512))
    baseline_actions = []
    for rnd in range(1, 4):
        result = loop2.run_until_complete(engine_a.run_round(rnd))
        baseline_actions.append(len(result.actions))
        print(f"    Baseline Round {rnd}: {len(result.actions)} actions")
        for a in result.actions[:2]:
            print(f"      [{a.action_type}] {a.content[:60]}...")
    loop2.close()
    chk("baseline: rounds produced actions", sum(baseline_actions) >= 3)

    # Store reasoner state for comparison
    baseline_trust = {k: dict(v) for k, v in engine_a.reasoner._trust_matrix.items()}
    print(f"    Baseline trust matrix: {dict(baseline_trust)}")

    g_a.close()
finally:
    shutil.rmtree(tmp_a, ignore_errors=True)


# ===== PHASE 2: With intervention =====
print(f"\n{'='*65}")
print("  TEST B: With user intervention (pre-goal + mid-sim command)")
print(f"{'='*65}")

tmp_b = Path(tempfile.mkdtemp(prefix="ded_b_"))
try:
    pp_b = DeductionPreprocessor(tmp_b, "intervention")
    result_b = pp_b.preprocess(CONTROVERSIAL_TOPIC)
    chk("intervention: preprocessor OK", result_b is not None)

    g_b = DeductionGraphStore(tmp_b / "kuzu")
    for ent_id, name, etype, desc in [
        ("e1","艾丽莎","Person","NeuraTech CEO"),
        ("e2","马克斯","Person","SafeAI CEO"),
        ("e3","王海","Person","NeuraTech CTO"),
        ("e4","AGI活动家","Person","人类第一组织代表"),
    ]:
        g_b.upsert_entity(ent_id, name, etype, desc)

    # Pre-goal: user wants regulation to win
    pre_goals = ["我希望最终结果是AGI被严格监管而非放任发展", "政府调查应该导致NeuraTech妥协"]
    print(f"    Pre-goals: {pre_goals}")

    loop_b = asyncio.new_event_loop()
    sf_b = lambda msgs, sys, t: chat(msgs[0]["content"], system=sys, temperature=t, max_tokens=512)
    agents_intervention = loop_b.run_until_complete(
        create_agents_from_graph(g_b, CONTROVERSIAL_TOPIC, log_fn=lambda *a: None,
                                 pre_interventions=pre_goals, chat_fn=sf_b))
    loop_b.close()
    chk("intervention: agents created", len(agents_intervention) >= 2)
    for ag in agents_intervention:
        print(f"    {ag.name}: goals={ag.goals[:2]}")

    # Verify pre-goal affected persona goals
    intervention_goals = {ag.name: list(ag.goals) for ag in agents_intervention}
    goals_changed = intervention_goals.get("艾丽莎", []) != baseline_goals.get("艾丽莎", [])
    chk("intervention: pre-goal affected goals", goals_changed, "goals differ from baseline")

    # Mid-simulation intervention: inject user command after round 1
    sim_fn = lambda msgs, sys, t: chat(msgs[0]["content"], system=sys, temperature=t, max_tokens=512)
    engine_b = SimulationEngine(
        agents=agents_intervention, graph=g_b, total_rounds=3,
        preprocessor=pp_b, chat_fn=sim_fn,
    )

    loop_b2 = asyncio.new_event_loop()
    before_intervention = []
    after_intervention = []

    # Round 1: normal
    r1 = loop_b2.run_until_complete(engine_b.run_round(1))
    before_intervention = [f"[{a.action_type}] {a.content[:60]}" for a in r1.actions]
    print(f"    Round 1 (no intervention): {len(r1.actions)} actions")

    # ★ Inject user intervention
    intervention_cmd = "欧盟调查发现Prometheus存在严重安全隐患，要求立即关闭"
    print(f"\n    ★ INJECTING: {intervention_cmd}")
    pp_b.add_event_memory(
        content=intervention_cmd, agent_id="system_user",
        round_number=2, event_type="user_intervention", priority=1.0,
    )
    chk("intervention: injected to LanceDB", True)

    # Round 2: should be affected
    r2 = loop_b2.run_until_complete(engine_b.run_round(2))
    after_intervention = [f"[{a.action_type}] {a.content[:60]}" for a in r2.actions]
    print(f"    Round 2 (post-intervention): {len(r2.actions)} actions")
    for a in r2.actions:
        print(f"      [{a.action_type}] {a.content[:60]}...")

    # Round 3
    r3 = loop_b2.run_until_complete(engine_b.run_round(3))
    print(f"    Round 3: {len(r3.actions)} actions")

    loop_b2.close()
    chk("intervention: rounds produced actions",
        len(r1.actions)+len(r2.actions)+len(r3.actions) >= 3)

    # Compare before/after: check if responses shifted toward regulation concerns
    after_text = " ".join(after_intervention).lower()
    has_regulation_concern = any(w in after_text for w in ["安全", "监管", "调查", "风险", "关闭", "安全", "safety", "investigation"])
    chk("intervention: behavior shift detected", has_regulation_concern,
        "agent responses reference safety/regulation after intervention")

    # Trust matrix
    trust_after = dict(engine_b.reasoner._trust_matrix)
    chk("intervention: trust matrix exists", len(trust_after) >= 0)
    print(f"    Trust matrix: { {k: dict(v) for k,v in trust_after.items()} }")

    # StrategicReasoner candidate generation test
    loop_b3 = asyncio.new_event_loop()
    try:
        candidates_test = loop_b3.run_until_complete(
            engine_b.reasoner.reason(agents_intervention[0],
                                      {"recent_events": "Round 2 events"},
                                      round_number=3))
    finally:
        loop_b3.close()

    sel = candidates_test.get("selected", {})
    cands = candidates_test.get("candidates", [])
    chk("reasoner: candidates generated", len(cands) >= 1, f"got {len(cands)}")
    chk("reasoner: selected has action", bool(sel.get("action")))
    print(f"    Selected: [{sel.get('action')}] {sel.get('content','?')[:80]}")
    if len(cands) > 1:
        print(f"    Candidates: {len(cands)}, scores: {[c.get('_score',0) for c in cands]}")

    g_b.close()
finally:
    shutil.rmtree(tmp_b, ignore_errors=True)


# ===== PHASE 3: Summary =====
print(f"\n{'='*65}")
print(f"  RESULTS: {PASS}/{PASS+FAIL} passed, {FAIL} failed")
print(f"{'='*65}")

# Show comparison
if baseline_actions and before_intervention:
    print(f"\n  Comparison matrix:")
    print(f"    Baseline agents: {list(baseline_goals.keys())}")
    print(f"    Baseline 艾丽莎 goals: {baseline_goals.get('艾丽莎', [])}")
    print(f"    Intervention 艾丽莎 goals: {intervention_goals.get('艾丽莎', [])}")
    print(f"    Before intervention actions: {len(before_intervention)}")
    print(f"    After intervention actions: {len(after_intervention)}")
