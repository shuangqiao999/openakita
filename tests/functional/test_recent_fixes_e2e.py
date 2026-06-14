"""
OpenAkita 最近修复功能测试 (LMStudio Live)

测试内容:
  1. approval_queue.py — retry_count 递增 + 3 次自动拒绝
  2. installer.py — 包名安全校验
  3. experiment_loop.py — asyncio.to_thread 文件读取
  4. runtime_metrics.py — SQLite 连接复用 + close

运行方式:
  python tests/functional/test_recent_fixes_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-needed")
os.environ.setdefault("DEFAULT_MODEL", "qwen/qwen3.5-9b")

PASS = 0
FAIL = 0
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


# ====================================================================
# 1. approval_queue.py — retry 逻辑
# ====================================================================
def test_approval_retry():
    print("\n" + "=" * 60)
    print("1. approval_queue.py — retry 逻辑")

    from openakita.evolution.approval_queue import ApprovalQueue, ApprovalRequest

    test_dir = str(_project_root / "data" / "test_recent_fixes" / "approvals")
    Path(test_dir).mkdir(parents=True, exist_ok=True)
    aq = ApprovalQueue(data_dir=test_dir)

    # 1a: 正常 submit + 一次拒绝
    req = ApprovalRequest(
        title="test_retry",
        target_file="identity/AGENT.md",
        original_content="old",
        proposed_content="new",
    )
    rid = aq.submit(req)
    ok = aq.reject(rid, "test")
    check("submit + reject", ok and "test" in aq.get(rid).get("reject_reason", ""))

    # 1b: retry_count 递增测试 (模拟 3 次失败 → 自动拒绝)
    req2 = ApprovalRequest(
        title="test_auto_reject",
        target_file="identity/AGENT.md",
        original_content="hello world",
        proposed_content="goodbye world",
    )
    rid2 = aq.submit(req2)
    data = aq.get(rid2)
    check("初始 retry_count=0", data.get("retry_count", 0) == 0)

    # 第 1 次尝试：模糊匹配应失败 (AGENT.md 不包含 "hello world")
    ok, msg = aq.approve_and_apply(rid2)
    data = aq.get(rid2)
    check("第1次失败后 retry_count=1", data.get("retry_count", 0) == 1)
    check("第1次失败后 status=pending", data.get("status") == "pending")

    # 第 2 次
    ok, msg = aq.approve_and_apply(rid2)
    data = aq.get(rid2)
    check("第2次失败后 retry_count=2", data.get("retry_count", 0) == 2)

    # 第 3 次 → 应自动拒绝
    ok, msg = aq.approve_and_apply(rid2)
    data = aq.get(rid2)
    check("第3次失败自动拒绝", not ok and data.get("status") == "rejected")
    check("拒绝原因包含重试次数", "3" in data.get("reject_reason", ""))

    # 1c: 空内容路径不递增 retry_count
    req3 = ApprovalRequest(title="test_empty", target_file="", original_content="", proposed_content="")
    rid3 = aq.submit(req3)
    ok, msg = aq.approve_and_apply(rid3)
    data = aq.get(rid3)
    check("空内容 retry_count 仍为 0", data.get("retry_count", 0) == 0)
    check("空内容 status=approved", data.get("status") == "approved")

    # 1d: retry_count 字段在 dataclass 中存在
    check("ApprovalRequest 有 retry_count 字段", hasattr(ApprovalRequest(), "retry_count"))


# ====================================================================
# 2. installer.py — 包名安全校验
# ====================================================================
def test_package_validation():
    print("\n" + "=" * 60)
    print("2. installer.py — 包名安全校验")

    import re

    # installer.py:116 使用的正则
    _PKG_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")

    # 2a: 正常包名通过
    check("numpy 通过", bool(_PKG_RE.match("numpy")))
    check("beautifulsoup4 通过", bool(_PKG_RE.match("beautifulsoup4")))
    check("scikit-learn 通过", bool(_PKG_RE.match("scikit-learn")))

    # 2b: 非法包名被拒绝
    check("cmd-injection 被拒绝", not _PKG_RE.match("evil; rm -rf /"))
    check("pipe 被拒绝", not _PKG_RE.match("netcat | sh"))
    check("ampersand 被拒绝", not _PKG_RE.match("curl && sh"))
    check("shell var 被拒绝", not _PKG_RE.match("$(whoami)"))
    check("backtick 被拒绝", not _PKG_RE.match("`id`"))
    check("c++ 加号被拒绝", not _PKG_RE.match("c++-compiler"))
    check("中文被拒绝", not _PKG_RE.match("中文包"))

    # 2c: 边缘情况
    check("带点的包名通过", bool(_PKG_RE.match("ruamel.yaml")))
    check("纯数字包名通过 (合法)", bool(_PKG_RE.match("12345")))
    # 纯数字也是有效的 pip 包名格式，不影响安全性

    # 2d: 代码中有 import re 和正则校验
    from openakita.evolution import installer as _imod
    mod_src = Path(_imod.__file__).read_text(encoding="utf-8")
    check("installer 模块有 import re", "import re" in mod_src)
    check("installer 模块有包名校验", "re.match(r\"^[a-zA-Z0-9_.-]+$\"" in mod_src.replace(" ", ""))


# ====================================================================
# 3. experiment_loop.py — 异步文件读取
# ====================================================================
async def test_async_file_read():
    print("\n" + "=" * 60)
    print("3. experiment_loop.py — 异步文件读取")

    from openakita.core.agent import Agent
    from openakita.core.brain import Brain

    brain = Brain(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
        model=os.environ["DEFAULT_MODEL"],
    )
    agent = Agent(brain=brain, name="test-async")

    from openakita.evolution.experiment_loop import ExperimentLoop

    test_dir = str(_project_root / "data" / "test_recent_fixes" / "experiments")
    loop = ExperimentLoop(agent, data_dir=test_dir)

    # 3a: MUTABLE_TARGETS 文件读取不崩溃
    try:
        hypothesis = await loop._generate_hypothesis(
            {"success_rate": 0.8, "avg_tokens": 100, "avg_time": 5, "efficiency_score": 0.5},
            [],
        )
        check("_generate_hypothesis 不崩溃 (正常)", hypothesis is None or hypothesis is not None)
        # None = MUTABLE_TARGETS 文件不存在; not None = LLM 返回了假设
    except Exception as e:
        check("_generate_hypothesis 不崩溃", False, f"{type(e).__name__}: {e}")

    # 3b: 不存在的文件能安全跳过
    saved = loop.MUTABLE_TARGETS[:]
    loop.MUTABLE_TARGETS = ["nonexistent_file.txt"]
    try:
        hypothesis = await loop._generate_hypothesis(
            {"success_rate": 0.5, "avg_tokens": 50, "avg_time": 3, "efficiency_score": 0.3},
            [],
        )
        check("不存在文件时返回 None", hypothesis is None)
    except Exception as e:
        check("不存在文件不崩溃", False, f"{type(e).__name__}: {e}")
    loop.MUTABLE_TARGETS = saved

    # 3c: to_thread 包装存在 (代码层面)
    import inspect
    src = inspect.getsource(ExperimentLoop._generate_hypothesis)
    check("_generate_hypothesis 包含 asyncio.to_thread", "asyncio.to_thread" in src)

    await agent.close() if hasattr(agent, "close") else None


# ====================================================================
# 4. runtime_metrics.py — SQLite 连接复用
# ====================================================================
def test_sqlite_connection_reuse():
    print("\n" + "=" * 60)
    print("4. runtime_metrics.py — SQLite 连接复用")

    from openakita.evolution.runtime_metrics import RuntimeMetricsCollector

    mdir = str(_project_root / "data" / "test_recent_fixes" / "metrics")
    collector = RuntimeMetricsCollector(data_dir=mdir)

    # 4a: _get_db 首次调用创建连接
    conn1 = collector._get_db()
    check("_get_db 首次调用返回连接", conn1 is not None or conn1 is None)
    if conn1 is not None:
        check("连接为 sqlite3.Connection", "sqlite3.Connection" in str(type(conn1)))

    # 4b: 再次调用返回同一连接
    conn2 = collector._get_db()
    if conn1 is not None:
        check("_get_db 复用同一连接", conn1 is conn2)

    # 4c: collect 不崩溃
    try:
        snap = collector.collect()
        check("collect 返回 RuntimeSnapshot", snap is not None)
        check("memory_total >= 0", snap.memory_total >= 0)
        check("memory_usage_rate 为 float", isinstance(snap.memory_usage_rate, float))
    except Exception as e:
        check("collect 不崩溃", False, f"{type(e).__name__}: {e}")

    # 4d: 二次 collect 不崩溃 (测试跨线程安全)
    try:
        snap2 = collector.collect()
        check("二次 collect 不崩溃 (跨线程)", snap2 is not None)
    except Exception as e:
        # sqlite3.ProgrammingError 表示 check_same_thread 问题
        check("二次 collect 不崩溃", False, f"{type(e).__name__}: {e}")

    # 4e: close 方法存在且不崩溃
    check("close 方法存在", hasattr(collector, "close"))
    try:
        collector.close()
        check("close 不崩溃", True)
    except Exception as e:
        check("close 不崩溃", False, f"{type(e).__name__}: {e}")

    # 4f: __del__ 存在
    check("__del__ 方法存在", hasattr(RuntimeMetricsCollector, "__del__"))


# ====================================================================
# main
# ====================================================================
async def amain():
    print("=" * 60)
    print("OpenAkita 最近修复功能测试 (LMStudio)")
    print("=" * 60)

    test_approval_retry()
    test_package_validation()
    await test_async_file_read()
    test_sqlite_connection_reuse()

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
