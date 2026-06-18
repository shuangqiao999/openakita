"""
jieba 中文分词器功能集成测试 (LMStudio 本地 API)

前置条件:
  - LMStudio 运行在 http://localhost:1234/v1
  - 已加载中文模型 (推荐 Qwen 系列)

运行:
    python tests/functional/test_jieba_lmstudio_live.py

测试:
  1. LMStudio 连通性
  2. 分词器基准 — 中文/英文/混合分词准确性
  3. SimHash 去重 — 中文 benchmark 描述不碰撞
  4. FTS5 索引与检索 — jieba 分词后 SQLite FTS5 中文搜索
  5. 记忆关键词搜索 — 中文查询命中中文记忆
  6. Benchmark 输出验证 — LLM 中文回答 vs 预期结果验证
  7. Jaccard 相似度 — 中文模式去重有效性
  8. 记忆重叠检测 — 中文记忆语义覆盖判断
  9. 关键词提取 — 从中文对话中提取有意义关键词
  10. 端到端 — LLM 生成中文内容 → 分词 → 验证
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-needed")
os.environ.setdefault("DEFAULT_MODEL", "qwen/qwen3.5-9b")

OUT_DIR = _project_root / "data" / "test_jieba_live"
PASS = FAIL = 0
FAILED: list[str] = []

LMSTUDIO_BASE = "http://localhost:1234/v1"
LMSTUDIO_TIMEOUT = 30


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
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


def _http_post(url: str, payload: dict, timeout: int = LMSTUDIO_TIMEOUT) -> dict:
    import urllib.error
    import urllib.request

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


def lmstudio_chat(prompt: str, max_tokens: int = 512) -> str:
    payload = {
        "model": "qwen/qwen3.5-9b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "stream": False,
    }
    result = _http_post(f"{LMSTUDIO_BASE}/chat/completions", payload)
    if "error" in result:
        return f"[ERROR] {result['error']}"
    try:
        return result["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return "[ERROR] unexpected response format"


def lmstudio_available() -> bool:
    try:
        import urllib.request

        req = urllib.request.Request(f"{LMSTUDIO_BASE}/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return "data" in data
    except Exception:
        return False


# ====================================================================
# 1. LMStudio 连通性
# ====================================================================
def test_1_connectivity():
    print("\n" + "=" * 60)
    print("1. LMStudio 连通性")

    ok = lmstudio_available()
    check("LMStudio API 可访问", ok)
    if not ok:
        print("  [SKIP] LMStudio 未启动, 跳过后续 LLM 依赖测试")
        return False

    resp = lmstudio_chat("只回复两个字: 你好", max_tokens=20)
    check("LLM 返回有效中文", "ERROR" not in resp and len(resp) > 0, resp[:50])
    return True


# ====================================================================
# 2. 分词器基准测试
# ====================================================================
def test_2_tokenizer_baseline():
    print("\n" + "=" * 60)
    print("2. 分词器基准 -- 中文/英文/混合")

    from openakita.core.tokenizer import extract_keywords, segment_text, tokenize_words

    cases = [
        ("纯中文", "深度学习在自然语言处理领域的最新应用研究",
         {"深度", "学习", "自然", "语言", "处理", "应用", "研究"}, 5),
        ("纯英文", "machine learning for natural language processing",
         {"machine", "learning", "natural", "language", "processing"}, 4),
        ("中英混合", "使用 Python 和 TensorFlow 训练中文 NLP 模型",
         {"python", "tensorflow", "训练", "中文", "模型"}, 5),
        ("长句", "我们公司最近在研究如何利用大语言模型提升客户服务质量和响应效率",
         {"公司", "研究", "语言", "模型", "客户", "服务", "质量", "效率"}, 5),
        ("带标点", '你好！请帮我搜索"机器学习"相关的文章，谢谢。',
         {"搜索", "机器", "学习", "文章"}, 3),
    ]

    for name, text, expected_subset, min_tokens in cases:
        tokens = tokenize_words(text)
        hit = len(expected_subset & tokens)
        total = len(expected_subset)
        check(
            f"{name}: >= {min_tokens} 词且命中 >= {total * 0.5:.0f}/{total}",
            len(tokens) >= min_tokens and hit >= total * 0.5,
            f"tokens={sorted(tokens)}, hit={hit}/{total}",
        )

    seg = segment_text("记忆模块性能优化方案讨论")
    check("segment_text 空格分隔", " " in seg, seg)

    kws = extract_keywords("如何利用深度学习技术改进推荐系统的准确率", top_k=5)
    check("extract_keywords top5", len(kws) <= 5 and len(kws) >= 2, str(kws))


# ====================================================================
# 3. SimHash 中文去重
# ====================================================================
def test_3_simhash_dedup():
    print("\n" + "=" * 60)
    print("3. SimHash 中文去重")

    from openakita.core.tokenizer import segment_text

    def simhash(text: str) -> str:
        words = sorted(set(segment_text(text.lower()).split()))
        return hashlib.md5(" ".join(words).encode()).hexdigest()

    descs = [
        "测试搜索引擎功能的基准评估任务",
        "评估文件读写操作的性能基准指标",
        "验证自然语言理解能力的综合测试",
        "检查代码生成工具的准确率和效率",
        "评估多轮对话中上下文保持的能力",
    ]
    hashes = [simhash(d) for d in descs]
    empty_hash = hashlib.md5("".encode()).hexdigest()

    check("5 个中文描述哈希互不相同", len(set(hashes)) == 5, str(hashes))
    check("中文哈希均非空串哈希", all(h != empty_hash for h in hashes))

    same_h1 = simhash("测试搜索引擎功能的基准评估任务")
    check("相同描述哈希一致", same_h1 == hashes[0])

    similar_h = simhash("搜索引擎优化的策略和技巧分享")
    check("不同主题描述哈希不同", similar_h != hashes[0], f"{similar_h} vs {hashes[0]}")

    reorder_h = simhash("评估搜索引擎功能的基准测试任务")
    check("近义重排序描述哈希相同 (去重正确)", reorder_h == hashes[0])


# ====================================================================
# 4. FTS5 中文索引与检索
# ====================================================================
def test_4_fts5_search():
    print("\n" + "=" * 60)
    print("4. FTS5 中文索引与检索")

    from openakita.core.tokenizer import segment_text

    db_path = OUT_DIR / "fts5_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(content)")

    docs = [
        "用户偏好设置: 喜欢使用 Python 编程, 偏好暗色主题",
        "项目部署说明: 使用 Docker 容器化部署到 Kubernetes 集群",
        "会议记录: 讨论了推荐系统的协同过滤算法改进方案",
        "技术笔记: TensorFlow 深度学习模型训练和调参技巧",
        "代码审查: 修复了内存泄漏问题并优化了查询性能",
    ]
    for doc in docs:
        segmented = segment_text(doc)
        conn.execute("INSERT INTO docs(content) VALUES (?)", (segmented,))
    conn.commit()

    queries = [
        ("Python", 1, "偏好"),
        ("推荐系统", 1, "协同"),
        ("Docker", 1, "部署"),
        ("深度学习", 1, "模型"),
        ("内存泄漏", 1, "修复"),
    ]
    for query, min_hits, must_contain in queries:
        seg_q = segment_text(query)
        terms = seg_q.split()
        fts_query = " OR ".join(f'"{t}"' for t in terms if len(t) >= 2)
        if not fts_query:
            fts_query = f'"{seg_q}"'
        try:
            rows = conn.execute(
                "SELECT content FROM docs WHERE docs MATCH ? LIMIT 5",
                (fts_query,),
            ).fetchall()
            found = any(must_contain in r[0] for r in rows)
            check(
                f"FTS5 '{query}' -> {len(rows)} 条, 含 '{must_contain}'",
                len(rows) >= min_hits and found,
                f"rows={len(rows)}, found={found}, fts_query={fts_query}",
            )
        except Exception as e:
            check(f"FTS5 '{query}'", False, str(e))

    conn.close()


# ====================================================================
# 5. 记忆关键词搜索
# ====================================================================
def test_5_memory_keyword_search():
    print("\n" + "=" * 60)
    print("5. 记忆关键词搜索")

    from openakita.core.tokenizer import tokenize_words

    memories = [
        {"id": "m1", "content": "用户喜欢使用 Vim 编辑器进行代码开发"},
        {"id": "m2", "content": "项目使用 React 和 TypeScript 构建前端界面"},
        {"id": "m3", "content": "数据库选用 PostgreSQL 并配置了读写分离"},
        {"id": "m4", "content": "部署环境为 Ubuntu 22.04 搭配 Nginx 反向代理"},
        {"id": "m5", "content": "团队使用飞书进行日常沟通和项目管理"},
    ]

    def keyword_search(query: str) -> list[str]:
        keywords = tokenize_words(query)
        hits = []
        for m in memories:
            content_tokens = tokenize_words(m["content"])
            if keywords & content_tokens:
                hits.append(m["id"])
        return hits

    cases = [
        ("搜索编辑器相关", "编辑器", ["m1"]),
        ("搜索前端技术", "前端", ["m2"]),
        ("搜索数据库", "数据库", ["m3"]),
        ("搜索部署环境", "部署", ["m4"]),
        ("搜索沟通工具", "飞书沟通", ["m5"]),
        ("英文搜索 React", "React", ["m2"]),
        ("无匹配", "游泳健身", []),
    ]
    for name, query, expected_ids in cases:
        hits = keyword_search(query)
        if expected_ids:
            ok = all(eid in hits for eid in expected_ids)
            check(name, ok, f"期望 {expected_ids}, 实际 {hits}")
        else:
            check(name, len(hits) == 0, f"期望空, 实际 {hits}")


# ====================================================================
# 6. Benchmark 输出验证 (LLM 驱动)
# ====================================================================
def test_6_verify_outcome_live(llm_ok: bool):
    print("\n" + "=" * 60)
    print("6. Benchmark 输出验证 (LLM)")

    from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkTask

    engine = BenchmarkEngine(data_dir=str(OUT_DIR / "bench6"))

    if not llm_ok:
        print("  [SKIP] LLM 不可用, 仅测试静态用例")

    static_cases = [
        ("中文匹配", "文件操作步骤和权限管理配置方法",
         "本文档详细介绍了文件操作的步骤和权限管理的配置方法", True),
        ("中文拒绝", "应该包含数据库优化建议",
         "今天天气很好适合出去散步", False),
        ("中英混合匹配", "输出应包含 Python 代码示例和函数说明",
         "以下是 Python 代码示例: def hello(): pass, 函数说明如上", True),
    ]
    for name, expected, output, want in static_cases:
        task = BenchmarkTask(
            id=f"t_{name}", description="test", category="test",
            expected_outcome=expected,
        )
        ok, reason = engine._verify_outcome(task, output)
        check(f"静态-{name}", ok == want, reason if ok != want else "")

    if llm_ok:
        prompt = "用一段话简单介绍 Python 的列表推导式。不要使用代码，只用中文文字描述。50字以内。"
        llm_output = lmstudio_chat(prompt, max_tokens=200)
        print(f"       LLM 回答: {llm_output[:80]}...")

        task = BenchmarkTask(
            id="t_llm_live", description="test", category="test",
            expected_outcome="列表推导式是Python中创建列表的简洁语法",
        )
        ok, reason = engine._verify_outcome(task, llm_output)
        check(f"LLM 验证: 列表推导式", ok, reason)

        from openakita.core.tokenizer import tokenize_words

        output_tokens = tokenize_words(llm_output)
        check("LLM 输出分词 >= 5 个有意义词", len(output_tokens) >= 5,
              f"实际: {len(output_tokens)} 词: {sorted(output_tokens)[:10]}")


# ====================================================================
# 7. Jaccard 中文相似度
# ====================================================================
def test_7_jaccard_similarity():
    print("\n" + "=" * 60)
    print("7. Jaccard 中文相似度")

    from openakita.evolution.pattern_learner import PatternLearner

    cases = [
        ("高相似", "使用搜索引擎查找技术文档", "调用搜索引擎检索技术资料", 0.2),
        ("中等相似", "Python 数据分析和可视化", "数据分析处理与图表展示", 0.1),
        ("低相似", "机器学习模型训练", "前端界面样式调整", 0.0),
        ("完全相同", "测试搜索功能", "测试搜索功能", 0.9),
    ]
    for name, a, b, min_j in cases:
        j = PatternLearner._jaccard_similarity(a, b)
        check(f"{name}: Jaccard={j:.3f} >= {min_j}", j >= min_j, f"实际: {j:.3f}")


# ====================================================================
# 8. 记忆重叠检测
# ====================================================================
def test_8_memory_overlap():
    print("\n" + "=" * 60)
    print("8. 记忆重叠检测")

    from openakita.tools.handlers.memory import MemoryHandler

    cases = [
        ("语义重叠", "用户喜欢 Python 编程", "用户偏好使用 Python 开发", True),
        ("同义替换", "项目部署在 Kubernetes 集群", "服务运行在 Kubernetes 环境", True),
        ("完全无关", "今天天气很好", "数据库查询优化", False),
        ("部分重叠", "机器学习模型训练", "深度学习模型部署", True),
    ]
    for name, left, right, want in cases:
        ok = MemoryHandler._has_meaningful_overlap(left, right)
        check(f"{name} -> {'重叠' if want else '无关'}", ok == want,
              f"实际: {'重叠' if ok else '无关'}")


# ====================================================================
# 9. 关键词提取
# ====================================================================
def test_9_keyword_extraction():
    print("\n" + "=" * 60)
    print("9. 关键词提取")

    from openakita.core.tokenizer import extract_keywords

    cases = [
        ("技术讨论", "我们需要讨论如何优化推荐系统的协同过滤算法以提升准确率",
         {"推荐", "系统", "协同", "过滤", "算法", "优化", "准确"}),
        ("项目需求", "前端使用React框架搭建管理后台并集成GraphQL接口",
         {"react", "graphql", "前端", "框架", "管理", "后台", "集成", "接口"}),
        ("用户偏好", "我喜欢使用暗色主题和Vim键位绑定来编写代码",
         {"暗色", "主题", "vim", "编写", "代码"}),
    ]
    for name, text, expected_subset in cases:
        kws = extract_keywords(text, top_k=8)
        kw_set = set(kws)
        hit = len(expected_subset & kw_set)
        check(
            f"{name}: 提取 {len(kws)} 词, 命中 {hit}/{len(expected_subset)}",
            hit >= 2 and len(kws) >= 3,
            f"关键词: {kws}",
        )


# ====================================================================
# 10. 端到端 LLM 分词验证
# ====================================================================
def test_10_e2e_llm_tokenize(llm_ok: bool):
    print("\n" + "=" * 60)
    print("10. 端到端 LLM 分词验证")

    if not llm_ok:
        print("  [SKIP] LLM 不可用")
        return

    from openakita.core.tokenizer import segment_text, tokenize_words

    prompt = "用中文列出3个Python常用的数据分析库，每个库名后面加一句简短的介绍。不要超过100字。"
    response = lmstudio_chat(prompt, max_tokens=300)
    print(f"       LLM 回答: {response[:100]}...")

    check("LLM 返回有效内容", "ERROR" not in response and len(response) > 20, response[:50])

    tokens = tokenize_words(response)
    print(f"       分词结果 ({len(tokens)} 词): {sorted(tokens)[:15]}...")
    check("分词结果 >= 10 个词", len(tokens) >= 10, f"实际 {len(tokens)} 个")
    check("过滤单字", all(len(t) >= 2 for t in tokens))

    has_lib_name = any(
        kw in response.lower()
        for kw in ["pandas", "numpy", "matplotlib", "scipy", "seaborn", "scikit"]
    )
    check("LLM 回答包含库名", has_lib_name, response[:80])

    if has_lib_name:
        lib_in_tokens = any(
            kw in tokens
            for kw in ["pandas", "numpy", "matplotlib", "scipy", "seaborn", "scikit"]
        )
        check("库名出现在分词结果中", lib_in_tokens, f"tokens: {sorted(tokens)[:20]}")

    seg = segment_text(response)
    seg_words = seg.split()
    check("segment_text 产出更多 token", len(seg_words) >= len(tokens) * 0.8,
          f"segment={len(seg_words)}, tokenize={len(tokens)}")

    from openakita.core.tokenizer import extract_keywords

    kws = extract_keywords(response, top_k=5)
    print(f"       关键词: {kws}")
    check("关键词提取 >= 3 个", len(kws) >= 3, str(kws))

    def simhash(text: str) -> str:
        words = sorted(set(segment_text(text.lower()).split()))
        return hashlib.md5(" ".join(words).encode()).hexdigest()

    h1 = simhash(response)
    h2 = simhash("完全不相关的内容关于天气预报")
    h_empty = hashlib.md5("".encode()).hexdigest()
    check("LLM 输出 SimHash 非空", h1 != h_empty)
    check("LLM 输出 SimHash 与无关内容不同", h1 != h2)


# ====================================================================
# main
# ====================================================================
def main():
    clean()
    print("=" * 60)
    print("  jieba 中文分词器功能集成测试 (LMStudio Live)")
    print("=" * 60)

    llm_ok = test_1_connectivity()
    test_2_tokenizer_baseline()
    test_3_simhash_dedup()
    test_4_fts5_search()
    test_5_memory_keyword_search()
    test_6_verify_outcome_live(llm_ok)
    test_7_jaccard_similarity()
    test_8_memory_overlap()
    test_9_keyword_extraction()
    test_10_e2e_llm_tokenize(llm_ok)

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
