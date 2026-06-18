"""
上线前全面验证脚本 (LMStudio qwen/qwen3.5-9b)

覆盖:
  1. LMStudio 连通性 + 模型确认
  2. 共享分词模块 — tokenize_words / segment_text / extract_keywords
  3. FTS5 索引兼容性 — jieba 分词写入 + 查询 + 旧 bigram 兼容
  4. 记忆存储→检索全链路 — SQLite 写入 + FTS5 搜索 + LIKE 回退
  5. SimHash 中文去重 — 不同/相同/近义描述
  6. Benchmark 完整运行 — 8 任务 LLM 推理 + 验证 + baseline 写入
  7. _verify_outcome 引号关键词 — 中文/英文/混合
  8. Baseline 管理 — 写入/更新/锚点加固
  9. 回归围栏 — 相对容差 + per-experiment 锚定
  10. 并发控制 — Semaphore 值 + 隔离性
  11. 记忆重叠检测 — 语义匹配 / 无关拒绝
  12. 关键词提取 — 中文/英文/混合
  13. Jaccard 中文相似度 — 梯度验证
  14. 工具搜索分词 — 中文工具名匹配
  15. task_health 追踪 — 失败计数 + 重置
  16. quality_weight 持久化 — 保存 + 读取一致
  17. 端到端 LLM — 生成→分词→SimHash→验证 全链路
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-needed")
os.environ.setdefault("DEFAULT_MODEL", "qwen/qwen3.5-9b")

LMSTUDIO_BASE = "http://localhost:1234/v1"
MODEL = "qwen/qwen3.5-9b"
OUT_DIR = _project_root / "data" / "test_prerelease"
PASS = FAIL = 0
FAILED: list[str] = []
SECTION = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(f"#{SECTION} {name}: {detail}" if detail else f"#{SECTION} {name}")
        print(f"  [FAIL] {name}  --  {detail}" if detail else f"  [FAIL] {name}")


def section(num: int, title: str):
    global SECTION
    SECTION = num
    print(f"\n{'=' * 60}")
    print(f"{num:2d}. {title}")
    print("-" * 60)


def clean():
    if OUT_DIR.exists():
        shutil.rmtree(str(OUT_DIR))
    OUT_DIR.mkdir(parents=True, exist_ok=True)


def _http_post(url, payload, timeout=60):
    import urllib.request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def llm_chat(prompt, max_tokens=512):
    result = _http_post(f"{LMSTUDIO_BASE}/chat/completions", {
        "model": MODEL, "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3, "max_tokens": max_tokens, "stream": False,
    })
    return result["choices"][0]["message"]["content"]


# ====================================================================
# 1. LMStudio
# ====================================================================
def test_01_connectivity():
    section(1, "LMStudio 连通性")
    import urllib.request
    try:
        req = urllib.request.Request(f"{LMSTUDIO_BASE}/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m["id"] for m in data.get("data", [])]
            check("API 可达", True)
            check(f"模型 {MODEL} 已加载", MODEL in models, f"可用: {models}")
            resp_text = llm_chat("只回复两个字: 你好", max_tokens=20)
            check("LLM 中文推理正常", len(resp_text) > 0 and "ERROR" not in resp_text)
            return MODEL in models
    except Exception as e:
        check("LMStudio 在线", False, str(e))
        return False


# ====================================================================
# 2. 共享分词模块
# ====================================================================
def test_02_tokenizer():
    section(2, "共享分词模块 core/tokenizer.py")
    from openakita.core.tokenizer import extract_keywords, segment_text, tokenize_words

    t1 = tokenize_words("深度学习在自然语言处理领域的应用")
    check("中文分词 >= 4 词", len(t1) >= 4, f"{len(t1)}: {sorted(t1)}")
    check("过滤单字", all(len(w) >= 2 for w in t1))

    t2 = tokenize_words("machine learning for NLP")
    check("英文分词包含 'machine'", "machine" in t2)

    t3 = tokenize_words("使用 Python 训练 BERT 模型")
    check("混合分词含中英", "python" in t3 and "训练" in t3, str(sorted(t3)))

    seg = segment_text("记忆模块性能优化")
    check("segment_text 含空格", " " in seg)

    kws = extract_keywords("如何利用深度学习改进推荐系统", top_k=3)
    check("extract_keywords 返回 <= 3", len(kws) <= 3 and len(kws) >= 1)
    check("关键词按长度降序", all(len(kws[i]) >= len(kws[i + 1]) for i in range(len(kws) - 1)))

    check("空文本安全", len(tokenize_words("")) == 0 and segment_text("") == "")


# ====================================================================
# 3. FTS5 索引兼容性
# ====================================================================
def test_03_fts5():
    section(3, "FTS5 索引兼容性")
    from openakita.core.tokenizer import segment_text

    db_path = OUT_DIR / "fts5_compat.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE VIRTUAL TABLE docs USING fts5(content)")

    old_bigram_data = [
        ("doc1", "记忆 忆模 模块 块性 性能 能优 优化"),
        ("doc2", "深度 度学 学习 习模 模型 型训 训练"),
    ]
    for doc_id, bigram_text in old_bigram_data:
        conn.execute("INSERT INTO docs(rowid, content) VALUES (?, ?)", (hash(doc_id) & 0x7FFFFFFF, bigram_text))

    jieba_data = [
        ("doc3", segment_text("自然语言处理技术在搜索引擎中的应用")),
        ("doc4", segment_text("Python 数据分析和机器学习实战")),
    ]
    for doc_id, seg_text in jieba_data:
        conn.execute("INSERT INTO docs(rowid, content) VALUES (?, ?)", (hash(doc_id) & 0x7FFFFFFF, seg_text))
    conn.commit()

    def fts_search(query):
        seg_q = segment_text(query)
        terms = [t for t in seg_q.split() if len(t) >= 2]
        fts_q = " OR ".join(f'"{t}"' for t in terms)
        return conn.execute("SELECT content FROM docs WHERE docs MATCH ?", (fts_q,)).fetchall()

    r1 = fts_search("记忆模块")
    check("jieba 查询命中旧 bigram 数据", len(r1) >= 1, f"结果数: {len(r1)}")

    r2 = fts_search("自然语言")
    check("jieba 查询命中 jieba 数据", len(r2) >= 1)

    r3 = fts_search("Python 机器学习")
    check("混合查询命中", len(r3) >= 1)

    r4 = fts_search("完全无关的天气预报")
    check("无关查询零命中", len(r4) == 0)

    conn.close()


# ====================================================================
# 4. 记忆存储→检索全链路
# ====================================================================
def test_04_memory_chain():
    section(4, "记忆存储→检索 (FTS5 + LIKE)")
    from openakita.core.tokenizer import segment_text, tokenize_words

    db_path = OUT_DIR / "memory_chain.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT, content_fts TEXT)")
    conn.execute("CREATE VIRTUAL TABLE memories_fts USING fts5(content)")

    memories = [
        ("m1", "用户偏好: 喜欢使用 Vim 编辑器，偏好暗色主题"),
        ("m2", "项目信息: 使用 React 和 TypeScript 构建前端"),
        ("m3", "技术栈: PostgreSQL 数据库配置了读写分离"),
        ("m4", "部署环境: Ubuntu 22.04 + Nginx 反向代理"),
        ("m5", "沟通方式: 团队使用飞书进行日常项目管理"),
    ]
    for mid, content in memories:
        seg = segment_text(content)
        conn.execute("INSERT INTO memories VALUES (?,?,?)", (mid, content, seg))
        conn.execute("INSERT INTO memories_fts(rowid, content) VALUES (?,?)", (hash(mid) & 0x7FFFFFFF, seg))
    conn.commit()

    def search_fts(query):
        seg_q = segment_text(query)
        terms = [t for t in seg_q.split() if len(t) >= 2]
        if not terms:
            return []
        fts_q = " OR ".join(f'"{t}"' for t in terms)
        try:
            return conn.execute(
                "SELECT content FROM memories_fts WHERE memories_fts MATCH ? LIMIT 5", (fts_q,)
            ).fetchall()
        except Exception:
            return []

    def search_like(query):
        from openakita.core.tokenizer import segment_text as seg
        keywords = seg(query.strip()).split()[:5]
        if not keywords:
            return []
        conditions = " OR ".join(["content LIKE ?"] * len(keywords))
        params = [f"%{kw}%" for kw in keywords]
        return conn.execute(f"SELECT content FROM memories WHERE {conditions}", params).fetchall()

    def search_keyword(query):
        kw_tokens = tokenize_words(query)
        hits = []
        for mid, content, _ in conn.execute("SELECT * FROM memories").fetchall():
            content_tokens = tokenize_words(content)
            if kw_tokens & content_tokens:
                hits.append(content[:30])
        return hits

    cases = [
        ("Vim 编辑器", "m1"),
        ("前端 React", "m2"),
        ("数据库", "m3"),
        ("部署 Nginx", "m4"),
        ("飞书", "m5"),
    ]
    for query, expected_id in cases:
        fts_r = search_fts(query)
        like_r = search_like(query)
        kw_r = search_keyword(query)
        any_hit = len(fts_r) > 0 or len(like_r) > 0 or len(kw_r) > 0
        check(f"查询 '{query}' 至少一种方式命中", any_hit,
              f"FTS={len(fts_r)}, LIKE={len(like_r)}, KW={len(kw_r)}")

    bad_r = search_fts("游泳健身跑步")
    check("无关查询不命中", len(bad_r) == 0)

    conn.close()


# ====================================================================
# 5. SimHash 中文去重
# ====================================================================
def test_05_simhash():
    section(5, "SimHash 中文去重")
    from openakita.core.tokenizer import segment_text

    def simhash(text):
        words = sorted(set(segment_text(text.lower()).split()))
        return hashlib.md5(" ".join(words).encode()).hexdigest()

    descs = [
        "测试搜索引擎功能的基准评估任务",
        "评估文件读写操作的性能指标",
        "验证自然语言理解能力的综合测试",
    ]
    hashes = [simhash(d) for d in descs]
    empty_h = hashlib.md5("".encode()).hexdigest()

    check("3 个中文描述哈希互不相同", len(set(hashes)) == 3)
    check("非空串哈希", all(h != empty_h for h in hashes))
    check("相同描述哈希一致", simhash(descs[0]) == hashes[0])


# ====================================================================
# 6. Benchmark 完整运行 (LLM)
# ====================================================================
_total_tokens = 0

@dataclass
class _FakeResult:
    success: bool = True
    data: str = ""
    error: str = ""
    iterations: int = 1

async def _task_runner(agent, desc):
    global _total_tokens
    try:
        output = await asyncio.to_thread(llm_chat, desc, 2048)
        _total_tokens += len(output) * 4
        return _FakeResult(success=True, data=output)
    except Exception as e:
        return _FakeResult(success=False, error=str(e))

def _token_counter(agent):
    return _total_tokens

async def test_06_benchmark(llm_ok):
    section(6, "Benchmark 完整运行 (8 任务 LLM 推理)")
    if not llm_ok:
        check("LLM 可用", False, "跳过")
        return None, None

    from openakita.evolution.benchmark import BenchmarkEngine

    bd = OUT_DIR / "benchmarks"
    engine = BenchmarkEngine(data_dir=str(bd), task_runner=_task_runner, token_counter=_token_counter)

    tasks = engine.load_tasks()
    print(f"  运行 {len(tasks)} 个任务 (模型: {MODEL})...")
    t0 = time.time()
    report = await engine.run_suite(None, tasks=tasks)
    elapsed = time.time() - t0
    m = report.metrics

    print(f"  成功率: {m.success_rate:.0%} ({sum(1 for r in report.results if r.success)}/{len(report.results)}), "
          f"耗时: {elapsed:.0f}s, 效率分: {m.efficiency_score:.1f}")
    for r in report.results:
        s = "PASS" if r.success else "FAIL"
        vr = f" [{r.verification_reason}]" if r.verification_reason else ""
        print(f"    [{s}] {r.task_id}: {r.time_seconds:.1f}s{vr}")

    check(f"成功率 >= 50% (实际 {m.success_rate:.0%})", m.success_rate >= 0.5)
    check(f"真实 LLM 推理 (耗时 {elapsed:.0f}s > 10s)", elapsed > 10)
    check("每个任务有耗时", all(r.time_seconds > 0 for r in report.results))

    engine.save_as_baseline(report)
    check("baseline.json 已写入", (bd / "baseline.json").exists())
    check("original_baseline.json 已写入", (bd / "original_baseline.json").exists())
    check("task_health.json 已写入", (bd / "task_health.json").exists())
    check("results/ 有结果文件", len(list((bd / "results").glob("*.json"))) >= 1)

    return engine, report


# ====================================================================
# 7. _verify_outcome 引号关键词
# ====================================================================
async def test_07_verify(llm_ok):
    section(7, "_verify_outcome 引号关键词实测")
    from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkTask

    engine = BenchmarkEngine(data_dir=str(OUT_DIR / "verify"))

    static = [
        ("引号中文匹配", "包含'机器学习'和'人工智能'", "机器学习是人工智能的分支", True),
        ("引号中文缺失", "包含'量子计算'", "今天天气很好", False),
        ("数字匹配", "输出 4950", "结果是 4950", True),
        ("数字缺失", "输出 4950", "结果是 5050", False),
        ("关键词回退匹配", "文件操作步骤权限管理配置方法",
         "本文档介绍了文件操作步骤和权限管理的配置方法", True),
        ("关键词回退拒绝", "数据库优化建议", "天气预报显示明天有雨", False),
    ]
    for name, expected, output, want in static:
        task = BenchmarkTask(id=f"v_{name}", description="t", category="t", expected_outcome=expected)
        ok, reason = engine._verify_outcome(task, output)
        check(f"静态-{name}", ok == want, reason if ok != want else "")

    if llm_ok:
        output = await asyncio.to_thread(llm_chat, "编写斐波那契函数计算 fib(10)", 512)
        task = BenchmarkTask(id="v_llm", description="t", category="coding",
                             expected_outcome="输出结果为 '55'")
        ok, reason = engine._verify_outcome(task, output)
        check(f"LLM 实测 fibonacci '55'", ok, reason)


# ====================================================================
# 8. Baseline 管理
# ====================================================================
def test_08_baseline():
    section(8, "Baseline 写入/更新/锚点加固")
    from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkMetrics, BenchmarkReport

    bd = OUT_DIR / "baseline_mgmt"
    engine = BenchmarkEngine(data_dir=str(bd))

    r1 = BenchmarkReport(timestamp="T1", metrics=BenchmarkMetrics(success_rate=0.5))
    engine.save_as_baseline(r1)
    check("首次 baseline=0.5", abs(json.loads((bd / "baseline.json").read_text("utf-8"))["metrics"]["success_rate"] - 0.5) < 0.01)
    check("首次 original=0.5", abs(json.loads((bd / "original_baseline.json").read_text("utf-8"))["metrics"]["success_rate"] - 0.5) < 0.01)

    r2 = BenchmarkReport(timestamp="T2", metrics=BenchmarkMetrics(success_rate=0.6))
    engine.save_as_baseline(r2)
    check("小幅提升 baseline=0.6", abs(json.loads((bd / "baseline.json").read_text("utf-8"))["metrics"]["success_rate"] - 0.6) < 0.01)
    check("小幅提升 original 不变=0.5 (delta=0.1<0.15)", abs(json.loads((bd / "original_baseline.json").read_text("utf-8"))["metrics"]["success_rate"] - 0.5) < 0.01)

    r3 = BenchmarkReport(timestamp="T3", metrics=BenchmarkMetrics(success_rate=1.0))
    engine.save_as_baseline(r3)
    check("大幅提升 baseline=1.0", abs(json.loads((bd / "baseline.json").read_text("utf-8"))["metrics"]["success_rate"] - 1.0) < 0.01)
    check("大幅提升 original 加固=1.0 (delta=0.5>0.15)", abs(json.loads((bd / "original_baseline.json").read_text("utf-8"))["metrics"]["success_rate"] - 1.0) < 0.01)


# ====================================================================
# 9. 回归围栏
# ====================================================================
def test_09_regression_guard():
    section(9, "回归围栏 (相对容差 + per-experiment)")
    from openakita.evolution.experiment_loop import (
        ExperimentLoop, _MAX_REGRESSION_TOLERANCE, _REGRESSION_GUARD_RATIO,
    )

    check("_REGRESSION_GUARD_RATIO = 0.80", abs(_REGRESSION_GUARD_RATIO - 0.80) < 0.001)
    check("_MAX_REGRESSION_TOLERANCE = 0.10", abs(_MAX_REGRESSION_TOLERANCE - 0.10) < 0.001)

    cases = [
        (1.0, 0.85, True, "anchor=1.0 current=0.85 > floor=0.80"),
        (1.0, 0.79, False, "anchor=1.0 current=0.79 < floor=0.80"),
        (0.8, 0.65, True, "anchor=0.8 current=0.65 > floor=0.64"),
        (0.8, 0.63, False, "anchor=0.8 current=0.63 < floor=0.64"),
    ]
    for anchor, current, should_pass, desc in cases:
        floor = anchor * _REGRESSION_GUARD_RATIO
        check(f"围栏: {desc}", (current >= floor) == should_pass)

    old = {"success_rate": 0.85, "avg_tokens": 100, "avg_time": 1.0}
    anchor = {"success_rate": 1.0}
    check("per-exp sr=0.91>anchor-0.10 通过",
          ExperimentLoop._is_improvement(old, {"success_rate": 0.91, "avg_tokens": 90, "avg_time": 0.9},
                                          threshold=0.0, anchor_metrics=anchor))
    check("per-exp sr=0.86<anchor-0.10 拒绝",
          not ExperimentLoop._is_improvement(old, {"success_rate": 0.86, "avg_tokens": 90, "avg_time": 0.9},
                                              threshold=0.0, anchor_metrics=anchor))


# ====================================================================
# 10. 并发控制
# ====================================================================
def test_10_concurrency():
    section(10, "Benchmark 并发控制 Semaphore")
    from openakita.scheduler.executor import _get_benchmark_sem

    sem = _get_benchmark_sem()
    check("返回 Semaphore 实例", isinstance(sem, asyncio.Semaphore))
    check("多次调用同一实例", sem is _get_benchmark_sem())

    import inspect
    from openakita.scheduler.executor import TaskExecutor
    src_be = inspect.getsource(TaskExecutor._system_benchmark_evolve)
    src_ro = inspect.getsource(TaskExecutor._system_research_org)
    check("benchmark_evolve 使用信号量", "_get_benchmark_sem" in src_be)
    check("research_org 使用信号量", "_get_benchmark_sem" in src_ro)


# ====================================================================
# 11. 记忆重叠检测
# ====================================================================
def test_11_overlap():
    section(11, "记忆重叠检测")
    from openakita.tools.handlers.memory import MemoryHandler

    cases = [
        ("语义重叠", "用户喜欢 Python 编程", "用户偏好使用 Python 开发", True),
        ("同义替换", "部署在 Kubernetes 集群", "服务运行在 Kubernetes 环境", True),
        ("完全无关", "今天天气很好", "数据库查询优化", False),
    ]
    for name, left, right, want in cases:
        ok = MemoryHandler._has_meaningful_overlap(left, right)
        check(f"{name} → {'重叠' if want else '无关'}", ok == want)


# ====================================================================
# 12. 关键词提取
# ====================================================================
def test_12_keywords():
    section(12, "关键词提取")
    from openakita.core.tokenizer import extract_keywords

    kws = extract_keywords("我们需要讨论推荐系统的协同过滤算法改进方案", top_k=5)
    check(f"提取 {len(kws)} 词 (<=5)", len(kws) <= 5 and len(kws) >= 2)

    expected = {"推荐", "系统", "协同", "过滤", "算法", "推荐系统", "协同过滤"}
    hit = len(set(kws) & expected)
    check(f"命中关键词 >= 2 (实际 {hit})", hit >= 2, str(kws))


# ====================================================================
# 13. Jaccard 中文相似度
# ====================================================================
def test_13_jaccard():
    section(13, "Jaccard 中文相似度")
    from openakita.evolution.pattern_learner import PatternLearner

    cases = [
        ("完全相同", "测试搜索功能", "测试搜索功能", 0.9),
        ("高相似", "搜索引擎查找文档", "搜索引擎检索资料", 0.2),
        ("低相似", "机器学习训练", "前端样式调整", 0.0),
    ]
    for name, a, b, min_j in cases:
        j = PatternLearner._jaccard_similarity(a, b)
        check(f"{name}: J={j:.2f} >= {min_j}", j >= min_j)


# ====================================================================
# 14. 工具搜索分词
# ====================================================================
def test_14_tool_search():
    section(14, "工具搜索分词")
    from openakita.tools.handlers.tool_search import _tokenize

    tokens = _tokenize("搜索文件内容")
    check(f"中文工具搜索分词 >= 2 词", len(tokens) >= 2, str(tokens))
    check("无单字 token", all(len(t) >= 2 for t in tokens))

    en_tokens = _tokenize("search web content")
    check("英文分词包含 'search'", "search" in en_tokens)


# ====================================================================
# 15. task_health
# ====================================================================
def test_15_task_health():
    section(15, "task_health 追踪")
    from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkResult, BenchmarkTask

    bd = OUT_DIR / "health"
    engine = BenchmarkEngine(data_dir=str(bd))
    tasks = [BenchmarkTask(id="t1", description="d", category="c", expected_outcome="")]
    results_fail = [BenchmarkResult(task_id="t1", success=False)]
    engine._update_health(results_fail, tasks)
    h1 = json.loads((bd / "task_health.json").read_text("utf-8"))
    check("失败后 consecutive_fails=1", h1["t1"]["consecutive_fails"] == 1)

    engine._update_health(results_fail, tasks)
    h2 = json.loads((bd / "task_health.json").read_text("utf-8"))
    check("连续失败 consecutive_fails=2", h2["t1"]["consecutive_fails"] == 2)

    results_pass = [BenchmarkResult(task_id="t1", success=True)]
    engine._update_health(results_pass, tasks)
    h3 = json.loads((bd / "task_health.json").read_text("utf-8"))
    check("成功后 consecutive_fails=0", h3["t1"]["consecutive_fails"] == 0)


# ====================================================================
# 16. quality_weight
# ====================================================================
def test_16_quality_weight():
    section(16, "quality_weight 持久化")
    from openakita.evolution.experiment_loop import ExperimentLoop

    loop = ExperimentLoop.__new__(ExperimentLoop)
    loop._data_dir = OUT_DIR / "qw"
    loop._data_dir.mkdir(parents=True, exist_ok=True)
    loop._backups_dir = OUT_DIR / "qw_backups"
    loop._backups_dir.mkdir(parents=True, exist_ok=True)
    loop._get_config = lambda k, d: d

    w1 = loop._load_quality_weight()
    check(f"首次加载 >= 0.13 (实际 {w1})", w1 >= 0.13)
    check("quality_weight.json 已创建", (loop._data_dir / "quality_weight.json").exists())

    w2 = loop._load_quality_weight()
    check("二次加载一致", abs(w2 - w1) < 0.001)

    loop._save_quality_weight(0.20)
    w3 = loop._load_quality_weight()
    check("保存 0.20 后读取一致", abs(w3 - 0.20) < 0.001)


# ====================================================================
# 17. 端到端 LLM
# ====================================================================
async def test_17_e2e(llm_ok):
    section(17, "端到端 LLM 全链路")
    if not llm_ok:
        check("LLM 可用", False, "跳过")
        return

    from openakita.core.tokenizer import extract_keywords, segment_text, tokenize_words

    prompt = "用中文简述 Python 的 GIL 是什么，50字以内。"
    output = await asyncio.to_thread(llm_chat, prompt, 200)
    print(f"  LLM: {output[:80]}...")

    tokens = tokenize_words(output)
    check(f"分词 >= 5 词 (实际 {len(tokens)})", len(tokens) >= 5)
    check("过滤单字", all(len(t) >= 2 for t in tokens))

    seg = segment_text(output)
    check("segment_text 有效", " " in seg and len(seg) > 10)

    kws = extract_keywords(output, top_k=3)
    check(f"关键词提取 >= 2 (实际 {len(kws)})", len(kws) >= 2)

    def simhash(text):
        words = sorted(set(segment_text(text.lower()).split()))
        return hashlib.md5(" ".join(words).encode()).hexdigest()

    h1 = simhash(output)
    h2 = simhash("完全不相关的天气预报内容")
    check("SimHash 区分 LLM 输出 vs 无关内容", h1 != h2)

    from openakita.evolution.benchmark import BenchmarkEngine, BenchmarkTask
    engine = BenchmarkEngine(data_dir=str(OUT_DIR / "e2e"))
    task = BenchmarkTask(id="e2e", description="t", category="t",
                         expected_outcome="'GIL'是Python的全局解释器锁")
    ok, reason = engine._verify_outcome(task, output)
    check(f"LLM 输出验证 'GIL'", ok, reason)


# ====================================================================
# main
# ====================================================================
async def main():
    clean()
    print("=" * 60)
    print(f"  上线前全面验证 (LMStudio {MODEL})")
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    llm_ok = test_01_connectivity()
    test_02_tokenizer()
    test_03_fts5()
    test_04_memory_chain()
    test_05_simhash()
    engine, report = await test_06_benchmark(llm_ok)
    await test_07_verify(llm_ok)
    test_08_baseline()
    test_09_regression_guard()
    test_10_concurrency()
    test_11_overlap()
    test_12_keywords()
    test_13_jaccard()
    test_14_tool_search()
    test_15_task_health()
    test_16_quality_weight()
    await test_17_e2e(llm_ok)

    print("\n" + "=" * 60)
    print(f"  结果: {PASS} passed, {FAIL} failed ({PASS + FAIL} total)")
    if FAILED:
        print(f"\n  失败项 ({len(FAILED)}):")
        for f in FAILED:
            print(f"    x {f}")
    else:
        print("\n  ✓ 全部通过，可以上线发布")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
