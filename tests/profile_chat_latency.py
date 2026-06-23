"""对话交互延迟剖析器 — 连接本地 LM Studio，量化"用户发消息 → 看到回复"
每个阶段的耗时，定位是哪里在拖慢交互、影响"丝滑度"。

它驱动**真实**的热路径组件：
  1. 查询嵌入往返 (LM Studio /v1/embeddings)
  2. 向量检索 (UnifiedStore + 真实 LanceDB + .where())
  3. RetrievalEngine.retrieve() 短查询 (规则拆解，无 LLM)
  4. RetrievalEngine.retrieve() 长查询 (触发 think_lightweight 的 LLM 拆解往返)
     —— (长 - 短) ≈ 注入记忆前那次额外 LLM 拆解的代价
  5. 主对话 LLM 的 TTFT(首字延迟) + 生成速率 (流式 /v1/chat/completions)

最后给出"用户看到第一个字之前"的耗时拆解 + 瓶颈结论。

运行:  python tests/profile_chat_latency.py
要求:  LM Studio 在 http://localhost:1234，已加载 1 个 chat 模型 + 1 个 embedding 模型；
       lancedb / pyarrow 已安装。
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import tempfile
import time
import types
from pathlib import Path

import httpx

BASE = "http://localhost:1234/v1"
HEADERS = {"Authorization": "Bearer lm-studio", "Content-Type": "application/json"}


# --------------------------------------------------------------------------- #
# Model discovery
# --------------------------------------------------------------------------- #
def discover_models() -> tuple[str | None, str | None]:
    try:
        r = httpx.get(f"{BASE}/models", timeout=4.0)
        r.raise_for_status()
        ids = [m.get("id", "") for m in r.json().get("data", [])]
    except Exception as e:
        print(f"[FATAL] 无法连接 LM Studio @ {BASE}: {e}")
        return None, None
    embed = next((m for m in ids if "embed" in m.lower()), None)
    chat = next((m for m in ids if "embed" not in m.lower()), None)
    return chat, embed


def pct(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def summarize(name: str, samples: list[float]) -> dict:
    return {
        "name": name,
        "p50": statistics.median(samples) if samples else 0.0,
        "p95": pct(samples, 95),
        "mean": statistics.mean(samples) if samples else 0.0,
        "n": len(samples),
    }


# --------------------------------------------------------------------------- #
# A stub brain whose think_lightweight actually hits LM Studio (real decompose)
# --------------------------------------------------------------------------- #
class LMStudioBrain:
    def __init__(self, base: str, model: str) -> None:
        self.base = base
        self.model = model
        self.calls = 0

    async def think_lightweight(self, prompt, system=None, max_tokens=256, **kw):
        self.calls += 1
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system or ""},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{self.base}/chat/completions", json=payload, headers=HEADERS)
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"]["content"]
        return types.SimpleNamespace(content=txt)


# --------------------------------------------------------------------------- #
# Chat streaming TTFT + throughput
# --------------------------------------------------------------------------- #
def measure_chat_stream(model: str, system: str, user: str, max_tokens: int = 220):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    t0 = time.perf_counter()
    ttft = None
    chunks = 0
    with httpx.stream(
        "POST", f"{BASE}/chat/completions", json=payload, headers=HEADERS, timeout=120.0
    ) as r:
        for line in r.iter_lines():
            if not line:
                continue
            if line.startswith("data: "):
                line = line[6:]
            if line.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(line)
            except Exception:
                continue
            delta = (chunk.get("choices") or [{}])[0].get("delta", {})
            if delta.get("content"):
                if ttft is None:
                    ttft = time.perf_counter() - t0
                chunks += 1
    total = time.perf_counter() - t0
    gen = total - (ttft or 0)
    rate = (chunks / gen) if gen > 0 else 0.0
    return ttft or total, total, chunks, rate


# --------------------------------------------------------------------------- #
# Build the real retrieval stack on top of LM Studio + LanceDB
# --------------------------------------------------------------------------- #
SEED_MEMORIES = [
    "用户喜欢使用深色主题界面",
    "用户的母语是中文，偏好中文回复",
    "用户在做一个多智能体 AI 助手项目，叫 OpenAkita",
    "用户的后端用 Python 和 FastAPI",
    "用户的桌面端用 Tauri 和 React",
    "用户偏好简洁直接的回答，不要废话",
    "用户在 Windows 上开发，使用 PowerShell",
    "用户关心对话交互的响应速度和流畅度",
    "用户的记忆系统用 SQLite 加 LanceDB 向量库",
    "用户喜欢在回答里带上 file:line 形式的代码引用",
    "用户讨厌被反复确认，希望直接执行",
    "用户的本地大模型跑在 LM Studio 上",
    "用户用 qwen 系列模型做对话",
    "用户的嵌入模型是 embeddinggemma 300m",
    "用户最近在修复记忆系统的数据丢失问题",
    "用户最近在排查桌面端闪退",
    "用户偏好中文撰写提交信息",
    "用户的项目仓库在 github 的 openakita",
    "用户重视隐私和多租户隔离",
    "用户希望对话快速丝滑提升体验",
]


def build_stack(tmp: Path, chat_model: str, embed_model: str):
    from openakita.llm.embeddings import OpenAIEmbedding
    from openakita.memory.lancedb_backend import LanceDBBackend
    from openakita.memory.retrieval import RetrievalEngine
    from openakita.memory.types import MemoryPriority, MemoryType, SemanticMemory
    from openakita.memory.unified_store import UnifiedStore

    emb = OpenAIEmbedding(model_name=embed_model, api_base=BASE, api_key="lm-studio", dimension=768)
    backend = LanceDBBackend(persist_dir=str(tmp / "lancedb"), embedding_dim=0)
    backend._cached_embedder = emb
    backend._embedder_pinged = True
    backend._embedding_dim = 768
    backend._ensure_table(768)

    store = UnifiedStore(tmp / "mem.db", search_backend=backend)
    for content in SEED_MEMORIES:
        store.save_semantic(
            SemanticMemory(
                content=content,
                type=MemoryType.FACT,
                priority=MemoryPriority.LONG_TERM,
                importance_score=0.6,
            ),
            scope="user",
            user_id="default",
            workspace_id="default",
        )

    brain = LMStudioBrain(BASE, chat_model)
    engine = RetrievalEngine(store, brain=brain)
    return store, engine, emb, brain


# --------------------------------------------------------------------------- #
# Main profile
# --------------------------------------------------------------------------- #
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    chat_model, embed_model = discover_models()
    if not chat_model or not embed_model:
        print("需要 LM Studio 同时加载 1 个 chat 模型 + 1 个 embedding 模型。")
        return 1
    try:
        import lancedb  # noqa: F401
        import pyarrow  # noqa: F401
    except Exception:
        print("[FATAL] 需要 lancedb / pyarrow。")
        return 1

    print(f"LM Studio @ {BASE}")
    print(f"  chat 模型 : {chat_model}")
    print(f"  embed 模型: {embed_model}\n")

    tmp = Path(tempfile.mkdtemp(prefix="oa_profile_"))
    store, engine, emb, brain = build_stack(tmp, chat_model, embed_model)

    short_q = "深色主题"            # ≤20 字符 → 规则拆解，无 LLM
    long_q = "我之前说过我对界面主题和回复语言有什么偏好来着？"  # >20 → LLM 拆解
    recent = [{"role": "user", "content": long_q}]

    # warmup (模型加载 / 连接预热，不计入)
    print("预热中…")
    asyncio.run(emb.embed_query("预热"))
    measure_chat_stream(chat_model, "你是助手。", "说‘好’", max_tokens=5)
    engine.retrieve(short_q)

    N = 4
    emb_lat, vec_lat, ret_short = [], [], []
    ret_long_rules, ret_long_llm = [], []
    ttft_l, total_l, rate_l = [], [], []

    print(f"采样 N={N} …\n")
    for _ in range(N):
        t = time.perf_counter()
        asyncio.run(emb.embed_query(long_q))
        emb_lat.append(time.perf_counter() - t)

        t = time.perf_counter()
        store.search_semantic_scored(
            long_q, scope="user", scope_owner="", user_id="default", workspace_id="default"
        )
        vec_lat.append(time.perf_counter() - t)

        t = time.perf_counter()
        engine.retrieve(short_q, recent_messages=recent)
        ret_short.append(time.perf_counter() - t)

        # 长查询：默认(规则拆解，本次修复后的默认热路径)
        engine.set_llm_decompose(False)
        engine._decompose_cache.clear()
        t = time.perf_counter()
        engine.retrieve(long_q, recent_messages=recent)
        ret_long_rules.append(time.perf_counter() - t)

        # 长查询：开启 LLM 拆解(修复前的旧热路径，用于量化它的代价)
        engine.set_llm_decompose(True)
        engine._decompose_cache.clear()
        t = time.perf_counter()
        engine.retrieve(long_q, recent_messages=recent)
        ret_long_llm.append(time.perf_counter() - t)
        engine.set_llm_decompose(False)

        ttft, total, _chunks, rate = measure_chat_stream(
            chat_model,
            "你是 OpenAkita 助手，用简洁中文回答。",
            "根据我的偏好，帮我把界面和回复设置好。",
        )
        ttft_l.append(ttft)
        total_l.append(total)
        rate_l.append(rate)

    def ms(x):
        return f"{x * 1000:8.0f} ms"

    rows = [
        summarize("查询嵌入往返 (embed_query)", emb_lat),
        summarize("向量检索 search_semantic_scored", vec_lat),
        summarize("retrieve() 短查询(规则,无LLM)", ret_short),
        summarize("retrieve() 长查询[默认:规则](修复后)", ret_long_rules),
        summarize("retrieve() 长查询[LLM拆解](修复前)", ret_long_llm),
        summarize("主对话 TTFT 首字延迟", ttft_l),
        summarize("主对话 总耗时", total_l),
    ]

    print("=" * 74)
    print(f"{'阶段':<34}{'p50':>12}{'p95':>12}{'mean':>12}")
    print("-" * 74)
    for r in rows:
        print(f"{r['name']:<34}{ms(r['p50']):>12}{ms(r['p95']):>12}{ms(r['mean']):>12}")
    print("=" * 74)

    fast_rules = statistics.median(ret_long_rules)
    slow_llm = statistics.median(ret_long_llm)
    decompose_cost = max(0.0, slow_llm - fast_rules)
    pre_llm_before = slow_llm                      # 修复前：注入记忆前要等 LLM 拆解
    pre_llm_after = fast_rules                      # 修复后：默认规则拆解，几乎即时
    ttft = statistics.median(ttft_l)
    gen_rate = statistics.median(rate_l)
    time_before = pre_llm_before + ttft             # 修复前"首字前"总延迟
    time_after = pre_llm_after + ttft               # 修复后"首字前"总延迟

    print("\n关键结论:")
    print(f"  · 额外 LLM 查询拆解(think_lightweight)代价  ≈ {decompose_cost*1000:.0f} ms  "
          f"(占修复前长查询检索的 {decompose_cost/max(slow_llm,1e-6)*100:.0f}%)  [brain LLM 调用 {brain.calls} 次]")
    print(f"  · 记忆检索总开销 修复前(llm拆解)            ≈ {slow_llm*1000:.0f} ms"
          f"    修复后(规则,默认)           ≈ {fast_rules*1000:.0f} ms")
    print(f"  · 主对话首字延迟 TTFT                         ≈ {ttft*1000:.0f} ms")
    print(f"  · 生成速率                                    ≈ {gen_rate:.0f} chunk/s")
    print("  ⇒ 用户'看到第一个字'前的总延迟")
    print(f"         修复前 ≈ {time_before*1000:.0f} ms")
    print(f"         修复后 ≈ {time_after*1000:.0f} ms  (节省 {decompose_cost*1000:.0f} ms)")
    if decompose_cost > 0.5:
        print(f"\n  修复效果: 首字延迟从 ~{time_before*1000:.0f}ms 降到 ~{time_after*1000:.0f}ms"
              f" (减少 {decompose_cost/time_before*100:.0f}%)")
        print("  默认不再在注入记忆之前多跑一次串行的 LLM 拆解往返。"
              " 需要时可调用 engine.set_llm_decompose(True) 开启。")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
