"""
Deduction Engine functional test — direct LMStudio HTTP calls.
Bypasses LLMClient.chat() to avoid circular import issues.
Uses requests directly for LMStudio OpenAI-compatible API.
"""
import json, sys, time, shutil, tempfile, sqlite3, requests
from pathlib import Path

LMSTUDIO_CHAT = "http://127.0.0.1:1234/v1/chat/completions"
LMSTUDIO_EMBED = "http://127.0.0.1:1234/v1/embeddings"
CHAT_MODEL = "qwen/qwen3.5-9b"
EMBED_MODEL = "text-embedding-embeddinggemma-300m-qat"

TEST_SOURCE = """
2026年6月，中国科技公司星辰科技（StarTech）宣布成功研发新一代量子计算芯片"天枢"。
公司CEO李明在发布会上表示，该芯片将大幅提升AI训练速度，预计2027年量产。
竞争对手华光半导体（HuaGuang）同日宣布获得政府50亿元补贴，加速光子芯片研发。
行业分析师张三认为，量子计算与光子计算的路线之争将进入白热化阶段。
欧洲监管机构EUTech表示将对中国芯片企业开展反垄断调查。
星辰科技CTO王五回应称，公司始终遵守国际规则，欢迎公平竞争。
下游企业智能云（SmartCloud）宣布将率先采购天枢芯片建设AI训练中心。
"""

def chat(prompt: str, system: str = "") -> str:
    """Call LMStudio chat directly via HTTP."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    r = requests.post(LMSTUDIO_CHAT, json={
        "model": CHAT_MODEL, "messages": messages,
        "temperature": 0.3, "max_tokens": 1024,
    }, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def embed(texts: list[str]) -> list[list[float]]:
    r = requests.post(LMSTUDIO_EMBED, json={
        "model": EMBED_MODEL, "input": texts,
    }, timeout=60)
    r.raise_for_status()
    data = r.json()
    return [d["embedding"] for d in data["data"]]

# ════════════════════════════════════════════════════════

print("=" * 60)
print("  Deduction Engine — LMStudio Direct Test")
print("=" * 60)

# 1. Verify models
print("\n1. Model check...")
r = requests.get("http://127.0.0.1:1234/v1/models", timeout=5)
models = {m["id"] for m in r.json()["data"]}
assert CHAT_MODEL in models, f"Chat model {CHAT_MODEL} not loaded"
assert EMBED_MODEL in models, f"Embed model {EMBED_MODEL} not loaded"
print(f"  OK: {CHAT_MODEL}, {EMBED_MODEL}")

# 2. Embed test
print("\n2. Embedding test...")
vecs = embed(["测试文本A", "测试文本B"])
assert len(vecs) == 2
assert len(vecs[0]) == 768
print(f"  OK: dim={len(vecs[0])}, batch size={len(vecs)}")

# 3. Ontology test (LLM call)
print("\n3. Ontology generation (LLM)...")
ontology_prompt = f"""你是知识本体专家。分析以下文本，返回JSON定义实体类型和关系类型。
```json
{{"entities":[{{"name":"Person","description":"人物"}}], "relations":[{{"name":"works_for","from_type":"Person","to_type":"Organization"}}]}}
```
规则: 不超过10种实体,15种关系。只返回JSON。
文本: {TEST_SOURCE[:3000]}"""
response = chat(ontology_prompt, "只输出JSON")
print(f"  Raw: {response[:200]}...")

import re
match = re.search(r"\{[\s\S]*\}", response)
assert match, "No JSON in ontology response"
ontology_data = json.loads(match.group(0))
entities = ontology_data.get("entities", [])
relations = ontology_data.get("relations", [])
print(f"  Entities: {[e['name'] for e in entities]}")
print(f"  Relations: {[r['name'] for r in relations]}")
assert len(entities) >= 2, f"Too few entities: {len(entities)}"
assert len(relations) >= 1, f"Too few relations: {len(relations)}"

# 4. GraphRAG extraction test
print("\n4. GraphRAG entity extraction (LLM)...")
extract_prompt = f"""从以下文本抽取实体和关系三元组。只返回JSON数组。
实体类型: {', '.join(e['name'] for e in entities)}
关系类型: {', '.join(r['name'] for r in relations)}
格式:
[{{"entity":"实体名","type":"类型","description":"描述"}},
 {{"source":"实体A","target":"实体B","relation":"关系","evidence":"原文证据"}}]

文本: {TEST_SOURCE[:2000]}"""
response2 = chat(extract_prompt, "只输出JSON数组")
match2 = re.search(r"\[[\s\S]*\]", response2)
assert match2, "No JSON array in extraction response"
triples = json.loads(match2.group(0))
extracted_entities = [t for t in triples if "entity" in t]
extracted_relations = [t for t in triples if "source" in t]
print(f"  Triples: {len(triples)}, entities={len(extracted_entities)}, relations={len(extracted_relations)}")
assert len(extracted_entities) >= 2, f"Too few entities: {len(extracted_entities)}"
assert len(extracted_relations) >= 1, f"Too few relations: {len(extracted_relations)}"

# 5. Kuzu graph store + extraction → graph
print("\n5. Kuzu graph store + extraction integration...")
from openakita.deduction.store import DeductionGraphStore

tmp = Path(tempfile.mkdtemp(prefix="ded_test_"))
try:
    graph = DeductionGraphStore(tmp / "test_graph")

    for ent in extracted_entities:
        eid = ent.get("entity", "unknown")[:20].encode().hex()[:12]
        graph.upsert_entity(eid, ent.get("entity", "?"), ent.get("type", "Concept"),
                           ent.get("description", ""))

    for rel in extracted_relations:
        sid = rel.get("source", "")[:20].encode().hex()[:12]
        tid = rel.get("target", "")[:20].encode().hex()[:12]
        graph.upsert_relation(sid, tid, rel.get("relation", "relates_to"),
                             evidence=rel.get("evidence", ""))

    e_count = graph.count_entities()
    r_count = graph.count_relations()
    print(f"  Kuzu: {e_count} entities, {r_count} relations")

    data = graph.export_graph_data()
    print(f"  Graph export: {len(data['nodes'])} nodes, {len(data['links'])} links")
    assert e_count >= 2
    assert r_count >= 1

    graph.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# 6. Agent persona test (LLM)
print("\n6. Agent persona generation (LLM)...")
persona_prompt = """基于以下信息生成人物人格档案。只返回JSON。
名称: 李明, 类型: Person, 描述: 星辰科技CEO
背景: 李明在发布会上宣布量子计算芯片天枢发布。
格式: {"persona":"人格描述50-100字","background":"背景故事","goals":["目标1","目标2"]}"""
response3 = chat(persona_prompt, "只输出JSON")
match3 = re.search(r"\{[\s\S]*\}", response3)
persona_data = json.loads(match3.group(0)) if match3 else {}
print(f"  Persona: {persona_data.get('persona', '?')[:80]}...")
print(f"  Goals: {persona_data.get('goals', [])}")
assert "persona" in persona_data
assert len(persona_data.get("persona", "")) >= 10

# 7. Simulation action test (LLM)
print("\n7. Agent decision simulation (LLM)...")
action_prompt = """根据角色设定决定下一步行动。只返回JSON。
角色: 积极乐观，技术创新倡导者
背景: 参与量子计算芯片发布
目标: 推动技术落地, 应对监管调查
轮次: 1
事件: [李明] post: 天枢芯片是量子计算的重大突破
格式: {"action":"post|reply|interact|observe","content":"行动内容30-100字"}"""
response4 = chat(action_prompt, "只输出JSON")
match4 = re.search(r"\{[\s\S]*\}", response4)
action_data = json.loads(match4.group(0)) if match4 else {}
print(f"  Action: {action_data.get('action', '?')} — {action_data.get('content', '?')[:80]}")
assert action_data.get("action") in ("post", "reply", "interact", "observe", "")

# 8. Report generation test (LLM)
print("\n8. Report generation (LLM)...")
report_prompt = """基于推演数据生成结构化报告。只返回JSON。
标题: 芯片行业推演
智能体: 3个, 模拟轮数: 2轮
关键事件:
- [轮1] 李明: post — 天枢芯片发布
- [轮1] 张三: reply — 光子芯片也重要
- [轮2] 王五: interact — 关注监管调查
格式: {"summary":"总结100-200字","risk_alerts":["风险"],"recommendations":["建议"]}"""
response5 = chat(report_prompt, "只输出JSON")
match5 = re.search(r"\{[\s\S]*\}", response5)
report_data = json.loads(match5.group(0)) if match5 else {}
print(f"  Summary: {report_data.get('summary', '?')[:120]}...")
print(f"  Risks: {report_data.get('risk_alerts', [])}")
print(f"  Recs: {report_data.get('recommendations', [])}")
assert "summary" in report_data
assert len(report_data.get("summary", "")) >= 20

# ════════════════════════════════════════════════════
print(f"\n{'='*60}")
print("  ALL TESTS PASSED — 8/8")
print("=" * 60)
