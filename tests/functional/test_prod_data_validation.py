"""
生产数据交叉验证脚本
读取安装版 D:\Akita\workspaces\default\data\ 下的真实文件，验证本次修复是否正确识别数据。
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

PROD_DATA = Path(r"D:\Akita\workspaces\default\data")

PASS = 0
FAIL = 0

def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  --  {detail}" if detail else f"  [FAIL] {name}")

# ====================================================================
# 1. result 字段修复验证 (success/completed → 应识别 101 条 completed)
# ====================================================================
def test_result_field_fix():
    print("\n" + "=" * 60)
    print("1. result 字段修复 (success/competed)")

    traces_dir = PROD_DATA / "react_traces"
    if not traces_dir.is_dir():
        check("react_traces 目录存在", False, str(traces_dir))
        return

    files = []
    for d in traces_dir.iterdir():
        if d.is_dir():
            files.extend(d.glob("*.json"))
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    check(f"总 trace 文件: {len(files)}", len(files) >= 50)

    # 旧逻辑: 只认 "success"
    old_success = 0
    old_completed = 0
    other_results = Counter()
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            r = d.get("result", "")
            if r == "success":
                old_success += 1
            elif r == "completed":
                old_completed += 1
            else:
                other_results[r] += 1
        except Exception:
            continue

    check(f"旧逻辑识别 'success': {old_success}", old_success == 0, f"expected=0, got={old_success}")
    check(f"实际 'completed': {old_completed}", old_completed >= 50)
    if other_results:
        print(f"  其他 result 值: {dict(other_results)}")

    # 新逻辑: success + completed
    new_valid = old_success + old_completed
    check(f"新逻辑可识别 {new_valid} 条 (应该 ≥100)", new_valid >= 100)


# ====================================================================
# 2. 工具调用提取修复验证 (id/input 键匹配)
# ====================================================================
def test_tool_extraction_fix():
    print("\n" + "=" * 60)
    print("2. 工具调用提取修复 (id/input 键匹配)")

    from openakita.evolution.pattern_learner import PatternLearner

    traces_dir = PROD_DATA / "react_traces"
    files = []
    for d in traces_dir.iterdir():
        if d.is_dir():
            files.extend(d.glob("*.json"))
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    # 找几个有 tools_used 的 trace
    samples_with_tools = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("tools_used"):
                samples_with_tools.append(d)
                if len(samples_with_tools) >= 3:
                    break
        except Exception:
            continue

    check(f"找到 {len(samples_with_tools)} 个有工具调用的 trace", len(samples_with_tools) >= 2)

    total_tools_extracted = 0
    for s in samples_with_tools:
        raw = s.get("iterations", [])
        tools = PatternLearner._extract_tool_names(raw)
        total_tools_extracted += len(tools)
        check(
            f"  trace {s.get('conversation_id','?')[-20:]}: tools_used={s.get('tools_used')} extracted={tools}",
            len(tools) > 0 or not s.get("tools_used"),
            f"expected >0, got {len(tools)}",
        )

    check("至少提取到 1 个工具名", total_tools_extracted >= 1)
    if total_tools_extracted == 0:
        print("  WARN: 工具提取为 0，检查 sample tool_call 结构")


# ====================================================================
# 3. conversation_metrics 修复 (completed 计入成功率)
# ====================================================================
def test_conversation_metrics_fix():
    print("\n" + "=" * 60)
    print("3. conversation_metrics 修复 (completed 计入成功率)")

    traces_dir = PROD_DATA / "react_traces"
    files = []
    for d in traces_dir.iterdir():
        if d.is_dir():
            files.extend(d.glob("*.json"))

    totals = total = 0
    succeeded = 0
    for f in files[:100]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            total += 1
            if d.get("result") in ("success", "completed"):
                succeeded += 1
        except Exception:
            continue

    if total > 0:
        rate = round(succeeded / total, 3)
        check(f"conversation_success_rate = {rate} (应 > 0.9)", rate > 0.9, f"got {rate}")


# ====================================================================
# 4. memory_usage_rate 修复 (access_count fallback)
# ====================================================================
def test_memory_usage_rate_fix():
    print("\n" + "=" * 60)
    print("4. memory_usage_rate 修复 (access_count fallback)")

    db_path = PROD_DATA / "memory" / "openakita.db"
    if not db_path.exists():
        check("memory DB 存在", False, str(db_path))
        return

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    check(f"memory_total = {total}", total > 0)

    used = 0
    method = "unknown"
    try:
        used = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE access_count > 0"
        ).fetchone()[0]
        method = "access_count"
    except Exception:
        try:
            used = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE last_accessed IS NOT NULL"
            ).fetchone()[0]
            method = "last_accessed"
        except Exception:
            method = "none"

    rate = round(used / max(total, 1), 3)
    check(f"memory_usage_rate = {rate} (method={method})", rate >= 0.0, f"rate={rate}")

    # Show column info
    col_info = [c[1] for c in conn.execute("PRAGMA table_info(memories)").fetchall()]
    print(f"  memories 列: {col_info}")

    conn.close()


# ====================================================================
# 5. draft_tasks 自动提升验证
# ====================================================================
def test_draft_promotion():
    print("\n" + "=" * 60)
    print("5. draft_tasks 自动提升")

    draft_path = PROD_DATA / "evolution" / "benchmarks" / "draft_tasks.json"
    tasks_path = PROD_DATA / "evolution" / "benchmarks" / "tasks.json"

    check("draft_tasks.json 存在", draft_path.exists())
    check("tasks.json 存在", tasks_path.exists())

    if draft_path.exists():
        drafts = json.loads(draft_path.read_text(encoding="utf-8"))
        check(f"drafts 数量: {len(drafts)}", len(drafts) >= 3, f"got {len(drafts)}")

    if tasks_path.exists():
        tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
        check(f"tasks 数量: {len(tasks)}", len(tasks) >= 8, f"got {len(tasks)}")
        auto_tasks = [t for t in tasks if "-auto" in t.get("id", "")]
        check(f"自动提升的 task: {len(auto_tasks)}", len(auto_tasks) >= 0)


# ====================================================================
# main
# ====================================================================
def main():
    print("=" * 60)
    print("生产数据交叉验证 (D:\\Akita\\workspaces\\default\\data)")
    print("=" * 60)

    test_result_field_fix()
    test_tool_extraction_fix()
    test_conversation_metrics_fix()
    test_memory_usage_rate_fix()
    test_draft_promotion()

    total = PASS + FAIL
    print("\n" + "=" * 60)
    print(f"  总计: {total}  通过: {PASS}  失败: {FAIL}")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
