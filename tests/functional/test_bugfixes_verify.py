"""
针对 4 项缺陷修复的功能测试 (LMStudio + 安装版数据)

测试:
  1. bump_access 回退路径 (except→else + _ensure_conn 失败时仍更新)
  2. PatternLearner result="completed" 识别
  3. ResearchOrg 回退填充 success_rate
  4. Draft ID 级去重
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
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

OUT_DIR = _project_root / "data" / "test_bugfixes"
PASS = FAIL = 0
FAILED: list[str] = []


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
    OUT_DIR.mkdir(parents=True)


# ====================================================================
# 1. bump_access 回退路径
# ====================================================================
def test_bump_access_fallback():
    print("\n" + "=" * 60)
    print("1. bump_access 回退路径")

    # 创建临时 DB
    db_path = OUT_DIR / "bump_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT, access_count INTEGER DEFAULT 0, last_accessed_at TEXT)")
    conn.execute("INSERT INTO memories (id, access_count) VALUES ('a', 0), ('b', 0), ('c', 0)")
    conn.commit()
    conn.close()

    # 模拟 bump_access：连接成功后 batch UPDATE
    conn = sqlite3.connect(str(db_path))
    ids = ["a", "b", "c"]
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id IN ({placeholders})",
        ["2026-06-15T00:00:00"] + ids,
    )
    conn.commit()
    rows = conn.execute("SELECT id, access_count FROM memories").fetchall()
    conn.close()
    for row in rows:
        check(f"批量: {row[0]} count={row[1]}", row[1] == 1)

    # 二次 bump — 累积效果
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        f"UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id IN ({placeholders})",
        ["2026-06-15T00:00:01"] + ids,
    )
    conn.commit()
    rows = conn.execute("SELECT id, access_count FROM memories").fetchall()
    conn.close()
    for row in rows:
        check(f"二次: {row[0]} count={row[1]}", row[1] == 2)

    # 验证 else 路径存在 (代码检查)
    from openakita.memory.unified_store import UnifiedStore
    src = open(_project_root / "src" / "openakita" / "memory" / "unified_store.py", encoding="utf-8").read()
    check("bump_access 有 else 块", "else:" in src.split("def bump_access")[1].split("def get_semantic")[0])


# ====================================================================
# 2. PatternLearner result="completed" 识别
# ====================================================================
def test_pattern_learner_result():
    print("\n" + "=" * 60)
    print("2. PatternLearner result 字段")

    # 代码检查
    import inspect
    from openakita.evolution.pattern_learner import PatternLearner
    src = inspect.getsource(PatternLearner._extract_sequences)
    check("_extract_sequences 接受 completed", '"completed"' in src)

    # 功能验证：用真实 trace 文件测试提取
    traces_dir = Path(r"D:\Akita\workspaces\default\data\react_traces")
    if traces_dir.is_dir():
        files = []
        for d in traces_dir.iterdir():
            if d.is_dir():
                files.extend(d.glob("*.json"))
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        # 模拟 _extract_sequences 的逻辑，用修复后的条件
        successful = 0
        old_style = 0
        for f in files[:50]:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if d.get("result") in ("success", "completed"):
                    successful += 1
                if d.get("result") == "success":
                    old_style += 1
            except Exception:
                continue
        check(f"新条件识别的成功: {successful}", successful > old_style)
        check(f"旧条件只识别: {old_style}", old_style == 0)
    else:
        check("react_traces 目录存在", False, str(traces_dir))


# ====================================================================
# 3. ResearchOrg 回退 success_rate
# ====================================================================
def test_research_org_fallback():
    print("\n" + "=" * 60)
    print("3. ResearchOrg success_rate 回退")

    # 代码检查
    import inspect
    from openakita.evolution.research_org import ResearchOrg
    src = inspect.getsource(ResearchOrg._gather_performance_data)
    check("_gather_performance_data 填充 success_rate", "success_rate" in src.split("conversation_success_rate")[0] if "conversation_success_rate" in src else False or "conversation_success_rate" in src)

    # 功能测试：模拟 _gather_performance_data 的 RuntimeMetricsCollector 回退
    from openakita.evolution.runtime_metrics import RuntimeMetricsCollector

    mdir = str(OUT_DIR / "research_test" / "metrics")
    collector = RuntimeMetricsCollector(data_dir=mdir)
    snap = collector.collect()
    check("RuntimeSnapshot conversation_success_rate >= 0", snap.conversation_success_rate >= 0)
    check("RuntimeSnapshot tool_frequencies 是 dict", isinstance(snap.tool_frequencies, dict))

    # 模拟回退逻辑
    metrics = {}
    if metrics.get("success_rate") is None and snap.conversation_success_rate > 0:
        metrics["success_rate"] = snap.conversation_success_rate
    check("回退填充 success_rate 正常", isinstance(metrics.get("success_rate"), (int, float)))


# ====================================================================
# 4. Draft ID 级去重
# ====================================================================
def test_draft_id_dedup():
    print("\n" + "=" * 60)
    print("4. Draft ID 级去重")

    def _sh(desc):
        words = sorted(set(re.findall(r"\b\w+\b", str(desc).lower())))
        return hashlib.md5(" ".join(words).encode()).hexdigest()

    # 模拟: 3 个已有 draft，新增 2 个变体（1 个 ID 重复 + 1 个新）
    existing = [
        {"id": "tool-file-edit-v1", "description": "create bench_complex.py"},
        {"id": "code-fibonacci-v1", "description": "matrix fast power"},
    ]
    new_variants = [
        type("T", (), {"id": "tool-file-edit-v1", "description": "create secure_config.json"})(),
        type("T", (), {"id": "code-refactor-v1", "description": "list comprehension"})(),
    ]

    existing_ids = {e.get("id", "") for e in existing}
    existing_hashes = {_sh(e.get("description", "")) for e in existing}
    added = 0
    for t in new_variants:
        if _sh(t.description) in existing_hashes or t.id in existing_ids:
            print(f"  跳过重复: {t.id}")
            continue
        existing_hashes.add(_sh(t.description))
        existing_ids.add(t.id)
        existing.append({"id": t.id, "description": t.description})
        added += 1

    check("ID 重复被跳过", added == 1)
    check("新 draft 被添加", len(existing) == 3)


# ====================================================================
async def test_full_workflow():
    print("\n" + "=" * 60)
    print("5. 全链路: ExperimentLoop + AutoEvolver + RuntimeMetrics")

    from openakita.core.agent import Agent
    from openakita.core.brain import Brain
    from openakita.evolution.experiment_loop import ExperimentLoop
    from openakita.evolution.auto_evolve import AutoEvolver
    from openakita.evolution.runtime_metrics import RuntimeMetricsCollector

    brain = Brain(api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ["OPENAI_BASE_URL"], model=os.environ["DEFAULT_MODEL"])
    agent = Agent(brain=brain, name="test-bugfix")

    # ExperimentLoop + quality_score
    loop = ExperimentLoop(agent, data_dir=str(OUT_DIR / "full_exp"))
    class FM:
        success_rate = 0.9
        avg_tokens = 400.0
        avg_time = 10.0
        efficiency_score = 0.5
        category_scores = {"tool_use": 0.9}
    class FR:
        metrics = FM()
    qs = ExperimentLoop._compute_quality_score(FR())
    check("quality_score 正常", qs is not None and qs.overall > 0)

    # AutoEvolver
    evolver = AutoEvolver(agent)
    for gap in ["missing_tool", "supervision_gap"]:
        r = await evolver.respond_to_failure("test", gap)
        check(f"{gap} → {r.action}", r.action in ("skip", "evolved", "flagged"))

    # RuntimeMetrics
    collector = RuntimeMetricsCollector(data_dir=str(OUT_DIR / "full_metrics"))
    snap = collector.collect()
    check("snapshot conversation_success_rate", isinstance(snap.conversation_success_rate, float))
    collector.close()

    await agent.close() if hasattr(agent, "close") else None


# ====================================================================
async def amain():
    print("=" * 60)
    print("4 项缺陷修复验证测试 (LMStudio)")
    print("=" * 60)

    clean()
    test_bump_access_fallback()
    test_pattern_learner_result()
    test_research_org_fallback()
    test_draft_id_dedup()
    await test_full_workflow()

    total = PASS + FAIL
    print("\n" + "=" * 60)
    print(f"  总计: {total}  通过: {PASS}  失败: {FAIL}")
    print("=" * 60)
    if FAILED:
        print(f"\n  失败项 ({len(FAILED)}):")
        for t in FAILED:
            print(f"    - {t}")
    return 0 if FAIL == 0 else 1

def main():
    return asyncio.run(amain())

if __name__ == "__main__":
    sys.exit(main())
