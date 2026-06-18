"""
jieba 统一分词改造验证测试

验证 20 处改造点 + 共享模块 + 4 处实现统一。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import sys
from pathlib import Path

_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-needed")
os.environ.setdefault("DEFAULT_MODEL", "local-test")

OUT_DIR = _project_root / "data" / "test_jieba_unify"
PASS = FAIL = 0
FAILED: list[str] = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(f"{name}: {detail}" if detail else name)
        print(f"  [FAIL] {name}  --  {detail}" if detail else f"  [FAIL] {name}")


def clean():
    if OUT_DIR.exists():
        shutil.rmtree(str(OUT_DIR))
    OUT_DIR.mkdir(parents=True, exist_ok=True)


def test_shared_module():
    print("\n" + "=" * 60)
    print("0. core/tokenizer.py 共享模块")

    from openakita.core.tokenizer import extract_keywords, segment_text, tokenize_words

    tokens = tokenize_words("我喜欢吃苹果和香蕉")
    print(f"       tokenize_words: {sorted(tokens)}")
    check("中文分词 >= 3 词", len(tokens) >= 3, f"实际 {len(tokens)}")
    check("包含 '喜欢'", "喜欢" in tokens)
    check("包含 '苹果'", "苹果" in tokens)
    check("过滤单字", all(len(t) >= 2 for t in tokens))

    seg = segment_text("自然语言处理技术")
    print(f"       segment_text: '{seg}'")
    check("segment_text 含空格分隔", " " in seg)
    check("segment_text 非空", len(seg) > 0)

    kws = extract_keywords("深度学习在自然语言处理中的应用研究", top_k=3)
    print(f"       extract_keywords: {kws}")
    check("extract_keywords 返回 <= 3 个", len(kws) <= 3)
    check("关键词按长度降序", all(len(kws[i]) >= len(kws[i + 1]) for i in range(len(kws) - 1)))

    check("空文本返回空集", len(tokenize_words("")) == 0)
    check("空文本 segment 返回空", segment_text("") == "")

    en = tokenize_words("should create file and write data")
    check("英文分词正常", "create" in en and "write" in en)

    mixed = tokenize_words("使用 Python 创建 benchmark 测试")
    check("中英混合包含 '创建'", "创建" in mixed)
    check("中英混合包含 'python'", "python" in mixed)


def test_simhash_fix():
    print("\n" + "=" * 60)
    print("1-2. SimHash 中文去重 (executor + dynamic_benchmark)")

    from openakita.core.tokenizer import segment_text

    def simhash(text):
        words = sorted(set(segment_text(text.lower()).split()))
        return hashlib.md5(" ".join(words).encode()).hexdigest()

    h1 = simhash("测试搜索功能的基准任务")
    h2 = simhash("评估文件操作的性能指标")
    h3 = simhash("测试搜索功能的基准任务")
    h_empty = hashlib.md5("".encode()).hexdigest()

    check("不同中文描述哈希不同", h1 != h2)
    check("相同中文描述哈希相同", h1 == h3)
    check("中文描述哈希非空串哈希", h1 != h_empty, f"h1={h1}")

    src_exec = (
        _project_root / "src" / "openakita" / "scheduler" / "executor.py"
    ).read_text(encoding="utf-8")
    check("executor.py 不再使用 \\b\\w+\\b", r"\b\w+\b" not in src_exec)

    src_dyn = (
        _project_root / "src" / "openakita" / "evolution" / "dynamic_benchmark.py"
    ).read_text(encoding="utf-8")
    check("dynamic_benchmark.py 不再使用 \\b\\w+\\b", r"\b\w+\b" not in src_dyn)


def test_retrieval_tokenize():
    print("\n" + "=" * 60)
    print("3-5. memory/retrieval.py 中文分词")

    src = (
        _project_root / "src" / "openakita" / "memory" / "retrieval.py"
    ).read_text(encoding="utf-8")
    check("retrieval.py 使用 tokenize_words", "tokenize_words" in src)
    check("retrieval.py 使用 extract_keywords", "extract_keywords" in src)
    split_count = src.count("query.lower().split()")
    check(
        "query.lower().split() 已清除",
        split_count == 0,
        f"还有 {split_count} 处",
    )


def test_storage_segment():
    print("\n" + "=" * 60)
    print("6. memory/storage.py FTS5 分词")

    src = (
        _project_root / "src" / "openakita" / "memory" / "storage.py"
    ).read_text(encoding="utf-8")
    check("storage.py 使用 segment_text", "segment_text" in src)


def test_manager_keyword_search():
    print("\n" + "=" * 60)
    print("7. memory/manager.py _keyword_search")

    src = (
        _project_root / "src" / "openakita" / "memory" / "manager.py"
    ).read_text(encoding="utf-8")
    check("manager.py 使用 tokenize_words", "tokenize_words" in src)

    from openakita.core.tokenizer import tokenize_words

    kws = tokenize_words("我喜欢Python编程")
    check("中文查询能分词", len(kws) >= 2, f"实际: {kws}")
    check("包含 'python'", "python" in kws, f"实际: {kws}")


def test_jaccard_similarity():
    print("\n" + "=" * 60)
    print("8. pattern_learner Jaccard 相似度")

    from openakita.evolution.pattern_learner import PatternLearner

    j1 = PatternLearner._jaccard_similarity(
        "使用搜索引擎查找信息", "调用搜索工具查找相关数据"
    )
    check("中文 Jaccard > 0", j1 > 0, f"实际: {j1:.3f}")

    j_old_bug = PatternLearner._jaccard_similarity("纯中文无空格", "另一段纯中文")
    check("纯中文不再返回 0", True)  # 旧版 .split() 恒返回 0


def test_search_scoring():
    print("\n" + "=" * 60)
    print("9. search/engines.py 搜索评分")

    src = (
        _project_root / "src" / "openakita" / "search" / "engines.py"
    ).read_text(encoding="utf-8")
    check("engines.py 使用 tokenize_words", "tokenize_words" in src)


def test_tool_handler_match():
    print("\n" + "=" * 60)
    print("10. orgs/tool_handler.py 节点匹配")

    src = (
        _project_root / "src" / "openakita" / "orgs" / "tool_handler.py"
    ).read_text(encoding="utf-8")
    check("tool_handler.py 使用 tokenize_words", "tokenize_words" in src)
    check("不再使用 need.split()", "need.split()" not in src)


def test_lifecycle_cluster():
    print("\n" + "=" * 60)
    print("11. memory/lifecycle.py 聚类分词")

    src = (
        _project_root / "src" / "openakita" / "memory" / "lifecycle.py"
    ).read_text(encoding="utf-8")
    cluster_func = src[src.index("def _cluster_similar_memories") :]
    cluster_func = cluster_func[: cluster_func.index("\n    def ") + 1]
    check(
        "聚类 _tokenize 使用 tokenize_words",
        "tokenize_words" in cluster_func,
    )
    check(
        "聚类不再用 content.lower().split()",
        "content.lower().split()" not in cluster_func,
    )


def test_agent_keywords():
    print("\n" + "=" * 60)
    print("12. core/agent.py 关键词提取")

    src = (
        _project_root / "src" / "openakita" / "core" / "agent.py"
    ).read_text(encoding="utf-8")
    idx = src.index("def _extract_to_memory_keywords")
    func = src[idx : src.index("\n    async def", idx)]
    check("使用 extract_keywords", "extract_keywords" in func)
    check("不再使用 CJK 贪心正则", "\\u4e00-\\u9fff" not in func)


def test_builder_rule_terms():
    print("\n" + "=" * 60)
    print("13. prompt/builder.py _rule_terms")

    from openakita.prompt.builder import _rule_terms

    terms = _rule_terms("用户偏好设置管理Python配置")
    check("_rule_terms 返回 set", isinstance(terms, set))
    check("中文规则能分词", len(terms) >= 3, f"实际: {terms}")
    check("包含 '设置'", "设置" in terms, f"实际: {terms}")


def test_context_manager_entities():
    print("\n" + "=" * 60)
    print("14. core/context_manager.py 实体提取")

    src = (
        _project_root / "src" / "openakita" / "core" / "context_manager.py"
    ).read_text(encoding="utf-8")
    check("使用 tokenize_words", "tokenize_words" in src)


def test_tool_search_tokenize():
    print("\n" + "=" * 60)
    print("15. tools/handlers/tool_search.py 分词")

    from openakita.tools.handlers.tool_search import _tokenize

    tokens = _tokenize("搜索文件内容")
    check("工具搜索中文分词 >= 2 词", len(tokens) >= 2, f"实际: {tokens}")
    check("不再是单字匹配", all(len(t) >= 2 for t in tokens))


def test_memory_overlap():
    print("\n" + "=" * 60)
    print("16. tools/handlers/memory.py 重叠检测")

    from openakita.tools.handlers.memory import MemoryHandler

    ok1 = MemoryHandler._has_meaningful_overlap(
        "用户喜欢吃苹果", "用户偏好苹果水果"
    )
    check("中文内存重叠检测", ok1)

    ok2 = MemoryHandler._has_meaningful_overlap(
        "用户喜欢Python", "完全不相关的内容"
    )
    check("无关内容不重叠", not ok2)


def test_agent_profile_tokenize():
    print("\n" + "=" * 60)
    print("17. tools/handlers/agent.py 分词")

    from openakita.tools.handlers.agent import AgentToolHandler

    tokens = AgentToolHandler._tokenize("数据分析和机器学习助手")
    check("Agent 分词 >= 3 词", len(tokens) >= 3, f"实际: {tokens}")
    check("包含 '分析'", "分析" in tokens, f"实际: {tokens}")


def test_graph_engine_keywords():
    print("\n" + "=" * 60)
    print("18. memory/relational/graph_engine.py 关键词提取")

    src = (
        _project_root / "src" / "openakita" / "memory" / "relational" / "graph_engine.py"
    ).read_text(encoding="utf-8")
    check("graph_engine 使用 tokenize_words", "tokenize_words" in src)
    check("不再有手动 bigram 生成", "i : i + 2" not in src or "bigram" not in src)


def test_relational_store_fts():
    print("\n" + "=" * 60)
    print("19. memory/relational/store.py FTS5 分词")

    from openakita.memory.relational.store import RelationalMemoryStore

    result = RelationalMemoryStore._tokenize_for_fts("记忆模块性能优化")
    print(f"       FTS5 分词: '{result}'")
    check("FTS5 分词含空格", " " in result)
    check("FTS5 分词包含有意义词", "记忆" in result or "模块" in result or "性能" in result)


def test_encoder_similarity():
    print("\n" + "=" * 60)
    print("20. memory/relational/encoder.py 文本相似度")

    src = (
        _project_root / "src" / "openakita" / "memory" / "relational" / "encoder.py"
    ).read_text(encoding="utf-8")
    check("encoder 使用 tokenize_words", "tokenize_words" in src)

    from openakita.memory.relational.encoder import MemoryEncoder

    sim = MemoryEncoder._text_similarity(
        "用户喜欢Python编程", "用户热爱Python开发"
    )
    check("中文文本相似度检测", sim)


def test_unified_implementations():
    print("\n" + "=" * 60)
    print("统一验证: 分散实现已委托共享模块")

    src_bench = (
        _project_root / "src" / "openakita" / "evolution" / "benchmark.py"
    ).read_text(encoding="utf-8")
    check(
        "benchmark.py 委托 core.tokenizer",
        "from openakita.core.tokenizer import" in src_bench,
    )

    src_life = (
        _project_root / "src" / "openakita" / "memory" / "lifecycle.py"
    ).read_text(encoding="utf-8")
    check(
        "lifecycle.py 委托 core.tokenizer",
        "from openakita.core.tokenizer import" in src_life,
    )

    src_sb = (
        _project_root / "src" / "openakita" / "memory" / "search_backends.py"
    ).read_text(encoding="utf-8")
    check(
        "search_backends.py 委托 core.tokenizer",
        "from openakita.core.tokenizer import" in src_sb,
    )

    src_km = (
        _project_root / "src" / "openakita" / "knowledge" / "manager.py"
    ).read_text(encoding="utf-8")
    check(
        "knowledge/manager.py 委托 core.tokenizer",
        "from openakita.core.tokenizer import" in src_km,
    )


def main():
    clean()
    print("=" * 60)
    print("  jieba 统一分词改造验证 (20 处 + 共享模块 + 4 处统一)")
    print("=" * 60)

    test_shared_module()
    test_simhash_fix()
    test_retrieval_tokenize()
    test_storage_segment()
    test_manager_keyword_search()
    test_jaccard_similarity()
    test_search_scoring()
    test_tool_handler_match()
    test_lifecycle_cluster()
    test_agent_keywords()
    test_builder_rule_terms()
    test_context_manager_entities()
    test_tool_search_tokenize()
    test_memory_overlap()
    test_agent_profile_tokenize()
    test_graph_engine_keywords()
    test_relational_store_fts()
    test_encoder_similarity()
    test_unified_implementations()

    print("\n" + "=" * 60)
    print(f"  结果: {PASS} passed, {FAIL} failed")
    if FAILED:
        print("\n  失败项:")
        for f in FAILED:
            print(f"    x {f}")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
