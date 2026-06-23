"""End-to-end memory integration test against a *live* local LM Studio.

Exercises the real path: LM Studio OpenAI-compatible embeddings →
LanceDBBackend (real LanceDB) → UnifiedStore save/search. It validates the
fixes that can only be proven with a real vector backend:

* #6  LanceDB native `.where()` scope/owner pre-filter (tenant isolation at
      the vector layer, not just the SQLite re-check)
* hybrid/vector score normalization to [0, 1]
* real semantic retrieval (paraphrase → correct memory)

Requirements (auto-skips if missing):
* LM Studio running at http://localhost:1234 with an embedding model loaded
* lancedb / pyarrow installed in the environment

Run:  pytest tests/test_memory_lmstudio_integration.py -v -s
"""

from __future__ import annotations

import asyncio

import pytest

LMSTUDIO_BASE = "http://localhost:1234/v1"
EMBED_MODEL = "text-embedding-embeddinggemma-300m-qat"
EMBED_DIM = 768


# --------------------------------------------------------------------------- #
# Availability probe — skip the whole module if the prerequisites aren't met
# --------------------------------------------------------------------------- #
def _lmstudio_embed_model() -> str | None:
    try:
        import httpx
    except Exception:
        return None
    try:
        r = httpx.get(f"{LMSTUDIO_BASE}/models", timeout=4.0)
        r.raise_for_status()
        ids = [m.get("id", "") for m in r.json().get("data", [])]
    except Exception:
        return None
    if EMBED_MODEL in ids:
        return EMBED_MODEL
    # else pick any model whose id looks like an embedding model
    for mid in ids:
        if "embed" in mid.lower():
            return mid
    return None


_MODEL = _lmstudio_embed_model()
try:
    import lancedb  # noqa: F401
    import pyarrow  # noqa: F401

    _HAS_LANCE = True
except Exception:
    _HAS_LANCE = False

pytestmark = pytest.mark.skipif(
    _MODEL is None or not _HAS_LANCE,
    reason="LM Studio embedding model and/or lancedb not available",
)


def _make_embedder():
    from openakita.llm.embeddings import OpenAIEmbedding

    return OpenAIEmbedding(
        model_name=_MODEL,
        api_base=LMSTUDIO_BASE,
        api_key="lm-studio",
        dimension=EMBED_DIM,
    )


def _make_lance_backend(tmp_path):
    """A LanceDBBackend wired to the live LM Studio embedder, table ready."""
    from openakita.memory.lancedb_backend import LanceDBBackend

    backend = LanceDBBackend(persist_dir=str(tmp_path / "lancedb"), embedding_dim=0)
    if backend._lancedb is None:
        pytest.skip("lancedb failed to import at runtime")
    emb = _make_embedder()
    # Inject the live embedder and open the table at its dimension.
    backend._cached_embedder = emb
    backend._embedder_pinged = True
    backend._embedding_dim = EMBED_DIM
    backend._ensure_table(EMBED_DIM)
    assert backend.available, "LanceDB backend did not become available"
    return backend


# --------------------------------------------------------------------------- #
# 1. The embeddings endpoint actually works and has the expected dimension
# --------------------------------------------------------------------------- #
def test_lmstudio_embeddings_live():
    emb = _make_embedder()
    vec = asyncio.run(emb.embed_query("记忆系统的端到端测试"))
    assert isinstance(vec, list) and len(vec) == EMBED_DIM
    assert any(abs(x) > 1e-9 for x in vec)


# --------------------------------------------------------------------------- #
# 2. #6 — LanceDB .where() enforces scope/owner isolation at the vector layer
# --------------------------------------------------------------------------- #
def test_lancedb_scope_where_isolation(tmp_path):
    backend = _make_lance_backend(tmp_path)
    meta = lambda uid: {  # noqa: E731
        "type": "fact", "scope": "global", "scope_owner": "",
        "user_id": uid, "workspace_id": "default",
    }
    assert backend.add("alice1", "用户喜欢深色主题界面", meta("alice"))
    assert backend.add("bob1", "用户喜欢深色主题界面", meta("bob"))

    # alice's search must NEVER return bob's row (pre-filtered by .where()).
    res = backend.search(
        "深色主题", limit=10, scope="global", scope_owner="",
        user_id="alice", workspace_id="default", hybrid=False,
    )
    ids = {r[0] for r in res}
    assert ids == {"alice1"}, f"vector-layer tenant isolation failed: {ids}"

    # bob's search returns only bob's row.
    res_b = backend.search(
        "深色主题", limit=10, scope="global", scope_owner="",
        user_id="bob", workspace_id="default", hybrid=False,
    )
    assert {r[0] for r in res_b} == {"bob1"}

    # scores from the vector path are normalized into [0, 1].
    for _id, score in res + res_b:
        assert 0.0 <= score <= 1.0, f"score out of range: {score}"
    backend.close()


# --------------------------------------------------------------------------- #
# 3. Full flow through UnifiedStore: real semantic retrieval + isolation +
#    normalized scores
# --------------------------------------------------------------------------- #
def test_unified_store_semantic_fullflow(tmp_path):
    from openakita.memory.types import MemoryPriority, MemoryType, SemanticMemory
    from openakita.memory.unified_store import UnifiedStore

    backend = _make_lance_backend(tmp_path)
    store = UnifiedStore(tmp_path / "mem.db", search_backend=backend)

    def _mem(content: str) -> SemanticMemory:
        return SemanticMemory(
            content=content,
            type=MemoryType.FACT,
            priority=MemoryPriority.LONG_TERM,
            importance_score=0.6,
        )

    # alice's memories
    id_dark = store.save_semantic(
        _mem("用户喜欢使用深色主题界面"), scope="global", user_id="alice"
    )
    store.save_semantic(
        _mem("用户的母语是中文，偏好中文回复"), scope="global", user_id="alice"
    )
    # bob's memory (must never surface for alice)
    store.save_semantic(
        _mem("用户喜欢浅色主题和英文界面"), scope="global", user_id="bob"
    )

    scored = store.search_semantic_scored(
        "暗色模式 界面偏好",
        limit=5,
        scope="global",
        scope_owner="",
        user_id="alice",
        workspace_id="default",
    )
    assert scored, "semantic search returned no results"

    ids = {m.id for m, _s in scored}
    # Isolation: bob's memory id is never present.
    assert all(not m.content.startswith("用户喜欢浅色") for m, _s in scored), (
        "alice's search leaked bob's memory"
    )
    # Relevance: the dark-theme memory is among alice's results for this query.
    assert id_dark in ids, "relevant memory not retrieved for paraphrased query"
    # Normalized scores.
    scores = [s for _m, s in scored]
    assert all(0.0 <= s <= 1.0 for s in scores), f"scores out of [0,1]: {scores}"
    assert max(scores) == pytest.approx(1.0, abs=1e-6) or len(scores) == 1

    store.close()
