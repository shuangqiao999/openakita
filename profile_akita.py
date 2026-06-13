#!/usr/bin/env python
"""
OpenAkita 全链路性能剖析脚本 (LMStudio)

连接本地 LMStudio，对 Agent.chat() 执行一组真实对话测试，
记录各环节耗时和 token 消耗，输出结构化性能报告和瓶颈分析。

运行方式:
  python profile_akita.py --iterations 3 --output reports/

前置条件:
  1. LMStudio 运行于 http://localhost:1234/v1
  2. 已加载模型 (默认 qwen/qwen3.5-9b)
  3. openakita 已正确安装 (pip install -e ".[dev]")
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── 环境配置 ──────────────────────────────────────────────────
os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-needed")
os.environ.setdefault("DEFAULT_MODEL", "qwen/qwen3.5-9b")

_project_root = Path(__file__).parent.resolve()
sys.path.insert(0, str(_project_root / "src"))

logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")
logger = logging.getLogger("profile_akita")

# ── 测试集 ────────────────────────────────────────────────────
TEST_CASES: list[dict] = [
    {
        "id": "qa_simple",
        "category": "chat",
        "input": "请用一句话解释什么是 OpenAkita",
        "keywords": ["AI", "助手", "agent", "Agent", "多智能体", "人工智能"],
    },
    {
        "id": "tool_search",
        "category": "tool_use",
        "input": "请搜索 Python 3.12 有哪些新特性，并列出至少 3 个",
        "keywords": ["f-string", "type", "PEP"],
    },
    {
        "id": "code_gen",
        "category": "coding",
        "input": "请写一个计算斐波那契数列第 n 项的 Python 函数，然后计算出 f(10) 的值",
        "keywords": ["def", "55"],
    },
    {
        "id": "memory_recall",
        "category": "memory",
        "input": "请记住：我最喜欢的编程语言是 Rust，我的操作系统是 Windows。然后告诉我你记住了什么？",
        "keywords": ["Rust", "Windows"],
    },
    {
        "id": "multi_step",
        "category": "research",
        "input": (
            "分析这段代码的性能瓶颈并提出优化方案：\n"
            "def find_duplicates(lst):\n"
            "    result = []\n"
            "    for i in range(len(lst)):\n"
            "        for j in range(i+1, len(lst)):\n"
            "            if lst[i] == lst[j] and lst[i] not in result:\n"
            "                result.append(lst[i])\n"
            "    return result"
        ),
        "keywords": ["O(n", "set", "hash", "优化", "复杂"],
    },
    {
        "id": "long_context",
        "category": "writing",
        "input": (
            "请用一段话（不超过 100 字）总结以下内容的核心观点：\n\n"
            "Python 自 1991 年诞生以来，经历了从脚本语言到全栈开发语言的演变。"
            "其设计哲学强调代码可读性和简洁性，这得益于 Guido van Rossum 对语言一致性的坚持。"
            "Python 3 是一个重要的里程碑，虽然 Python 2 到 3 的迁移过程充满争议，"
            "但最终社区接受了这一变革。异步编程（asyncio）、类型注解（type hints）、"
            "模式匹配（match/case）等现代特性使 Python 在数据处理、机器学习、"
            "Web 开发等领域保持了强大竞争力。Python 的成功不仅在于语言本身，"
            "更在于其庞大的生态：PyPI 上超过 40 万个包，覆盖几乎所有编程领域。"
            "未来，Python 将继续在性能优化（如 Faster CPython 项目）和开发者体验方面持续精进。"
        ),
        "keywords": ["Python", "可读", "生态"],
    },
]


# ── 数据模型 ──────────────────────────────────────────────────
@dataclass
class LLMCallRecord:
    seq: int = 0
    model: str = ""
    duration_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ToolCallRecord:
    name: str = ""
    duration_ms: float = 0.0
    status: str = "unknown"


@dataclass
class TestResult:
    test_id: str = ""
    category: str = ""
    user_input: str = ""
    iteration: int = 0
    success: bool = False
    matched_keywords: list[str] = field(default_factory=list)
    response_preview: str = ""
    total_ms: float = 0.0
    llm_calls: list[LLMCallRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    tokens_input: int = 0
    tokens_output: int = 0
    overhead_ms: float = 0.0
    error: str = ""


# ── 核心剖析引擎 ─────────────────────────────────────────────
class ProfilingAgent:
    def __init__(self):
        from openakita.core.agent import Agent
        from openakita.core.brain import Brain

        model = os.environ["DEFAULT_MODEL"]
        self._brain = Brain(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ["OPENAI_BASE_URL"],
            model=model,
        )
        self._agent = Agent(brain=self._brain, name="profile-agent")
        self._tool_executor = getattr(self._agent, "tool_executor", None)

        # 采集器
        self._llc_records: list[LLMCallRecord] = []
        self._tool_records: list[ToolCallRecord] = []
        self._llm_seq = 0
        self._orig_brain_create = None
        self._orig_tool_exec = None

    def _install_hooks(self):
        self._llc_records = []
        self._tool_records = []
        self._llm_seq = 0

        brain = self._brain

        # ── Hook: brain._llm_client.chat (actual HTTP layer) ──
        if brain is not None:
            llm_client = getattr(brain, "_llm_client", None)
            if llm_client is not None:
                orig = llm_client.chat
            else:
                orig = getattr(brain, "messages_create_async", None)

            agent_self = self

            async def timed_llm(**kwargs):
                t0 = time.perf_counter()
                tokens_before = brain.total_tokens_used
                try:
                    result = await orig(**kwargs)
                except Exception:
                    raise
                finally:
                    elapsed = (time.perf_counter() - t0) * 1000
                    tokens_after = brain.total_tokens_used
                    agent_self._llm_seq += 1
                    agent_self._llc_records.append(
                        LLMCallRecord(
                            seq=agent_self._llm_seq,
                            model=getattr(brain, "model", ""),
                            duration_ms=round(elapsed, 1),
                            input_tokens=0,
                            output_tokens=max(0, tokens_after - tokens_before),
                        )
                    )
                return result

            if llm_client is not None:
                llm_client.chat = timed_llm
            else:
                brain.messages_create_async = timed_llm
            self._orig_brain_create = orig
            self._brain_client_patched = llm_client is not None

    def _restore_hooks(self):
        if self._orig_brain_create is not None:
            llm_client = getattr(self._brain, "_llm_client", None)
            patched = getattr(self, "_brain_client_patched", False)
            if patched and llm_client is not None:
                llm_client.chat = self._orig_brain_create
            else:
                self._brain.messages_create_async = self._orig_brain_create
        if self._orig_tool_exec is not None and self._tool_executor is not None:
            self._tool_executor.execute_batch = self._orig_tool_exec

    async def run_one(self, test: dict, iteration: int, conv_id: str) -> TestResult:
        self._install_hooks()
        tokens_before = self._brain.total_tokens_used
        error_msg = ""

        t0 = time.perf_counter()
        try:
            raw = await self._agent.chat(message=test["input"], session_id=conv_id)
        except Exception as e:
            raw = ""
            error_msg = f"{type(e).__name__}: {e}"
        elapsed = (time.perf_counter() - t0) * 1000

        tokens_after = self._brain.total_tokens_used
        self._restore_hooks()

        # 验证关键词命中
        matched = [kw for kw in test.get("keywords", []) if kw.lower() in raw.lower()]

        llm_total = sum(r.duration_ms for r in self._llc_records)
        tool_total = sum(r.duration_ms for r in self._tool_records)

        return TestResult(
            test_id=test["id"],
            category=test.get("category", ""),
            user_input=test["input"][:200],
            iteration=iteration,
            success=bool(matched) and not error_msg,
            matched_keywords=matched,
            response_preview=raw[:200],
            total_ms=round(elapsed, 1),
            llm_calls=list(self._llc_records),
            tool_calls=list(self._tool_records),
            tokens_input=0,
            tokens_output=max(0, tokens_after - tokens_before),
            overhead_ms=round(max(0, elapsed - llm_total - tool_total), 1),
            error=error_msg,
        )

    async def warmup(self) -> None:
        print("  预热中（排除冷启动影响）...")
        try:
            await self._agent.chat(message="你好，请回复 OK。", session_id="warmup_session")
        except Exception:
            pass

    async def close(self) -> None:
        self._restore_hooks()
        try:
            if hasattr(self._agent, "_finalize_all_sessions"):
                await self._agent._finalize_all_sessions()
        except Exception:
            pass


# ── 报告生成 ──────────────────────────────────────────────────
def build_report(results: list[TestResult], iterations: int, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── performance_report.json ──
    perf_data = []
    for r in results:
        perf_data.append(
            {
                "test_id": r.test_id,
                "category": r.category,
                "iteration": r.iteration,
                "success": r.success,
                "matched_keywords": r.matched_keywords,
                "response_preview": r.response_preview,
                "total_ms": r.total_ms,
                "llm_total_ms": round(sum(c.duration_ms for c in r.llm_calls), 1),
                "llm_call_count": len(r.llm_calls),
                "llm_calls": [asdict(c) for c in r.llm_calls],
                "tool_total_ms": round(sum(c.duration_ms for c in r.tool_calls), 1),
                "tool_calls": [asdict(c) for c in r.tool_calls],
                "tokens_input": r.tokens_input,
                "tokens_output": r.tokens_output,
                "overhead_ms": r.overhead_ms,
                "error": r.error,
            }
        )

    (output_dir / "performance_report.json").write_text(
        json.dumps(perf_data, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"  [OK] performance_report.json ({len(perf_data)} 条目)")

    # ── bottleneck_analysis.json ──
    by_test: dict[str, list[TestResult]] = {}
    for r in results:
        by_test.setdefault(r.test_id, []).append(r)

    summary = {
        "iterations": iterations,
        "test_count": len(TEST_CASES),
        "model": os.environ["DEFAULT_MODEL"],
    }
    test_summaries = []
    all_totals = []
    all_overheads = []
    all_llms = []
    all_tools = []
    all_tokens = []

    for tid in sorted(by_test):
        group = by_test[tid]
        totals = [r.total_ms for r in group]
        llms = [sum(c.duration_ms for c in r.llm_calls) for r in group]
        tools = [sum(c.duration_ms for c in r.tool_calls) for r in group]
        tokens = [r.tokens_output for r in group]
        overheads = [r.overhead_ms for r in group]
        successes = sum(1 for r in group if r.success)

        def _p(arr, p):
            return round(_percentile(sorted(arr), p), 1) if arr else 0

        test_summaries.append({
            "test_id": tid,
            "category": group[0].category,
            "success_rate": round(successes / len(group), 2) if group else 0,
            "avg_total_ms": round(statistics.mean(totals), 1),
            "p95_total_ms": _p(totals, 95),
            "avg_llm_ms": round(statistics.mean(llms), 1) if llms else 0,
            "avg_tool_ms": round(statistics.mean(tools), 1) if tools else 0,
            "avg_overhead_ms": round(statistics.mean(overheads), 1) if overheads else 0,
            "avg_output_tokens": round(statistics.mean(tokens), 0) if tokens else 0,
        })
        all_totals.extend(totals)
        all_overheads.extend(overheads)
        all_llms.extend(llms)
        all_tools.extend(tools)
        all_tokens.extend(tokens)

    # 全局聚合
    def _s(arr):
        arr_s = sorted(arr)
        return {
            "avg": round(statistics.mean(arr), 1),
            "median": round(statistics.median(arr), 1),
            "p95": _p(arr_s, 95),
            "p99": _p(arr_s, 99),
            "min": round(min(arr), 1),
            "max": round(max(arr), 1),
        }

    all_llm_total = sum(all_llms)
    all_tool_total = sum(all_tools)
    all_overhead_total = sum(all_overheads)
    grand_total = all_llm_total + all_tool_total + all_overhead_total

    breakdown = {
        "llm_calls": {
            **_s(all_llms),
            "percent": round(all_llm_total / max(grand_total, 1) * 100, 1),
        },
        "tool_calls": {
            **_s(all_tools),
            "percent": round(all_tool_total / max(grand_total, 1) * 100, 1),
        },
        "overhead": {
            **_s(all_overheads),
            "percent": round(all_overhead_total / max(grand_total, 1) * 100, 1),
        },
        "total": _s(all_totals),
    }

    # 瓶颈判定
    biggest_key = max(breakdown, key=lambda k: breakdown[k]["percent"] if k != "total" else 0)
    bottleneck_stage = biggest_key
    bottleneck_pct = breakdown[biggest_key]["percent"]

    analysis = {
        "summary": {
            **_s(all_totals),
            "avg_output_tokens": round(statistics.mean(all_tokens), 0),
            "total_tests_ran": len(results),
            "overall_success_rate": round(sum(1 for r in results if r.success) / max(len(results), 1), 2),
        },
        "by_test": test_summaries,
        "breakdown": breakdown,
        "bottleneck": {
            "stage": bottleneck_stage,
            "percent": bottleneck_pct,
            "avg_ms": breakdown[biggest_key]["avg"],
        },
        "slowest_tests": sorted(test_summaries, key=lambda t: -t["avg_total_ms"])[:3],
    }

    (output_dir / "bottleneck_analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"  [OK] bottleneck_analysis.json")

    # ── optimization_suggestions.txt ──
    suggestions = []
    suggestions.append("OpenAkita 性能剖析 —— 优化建议")
    suggestions.append("=" * 50)
    suggestions.append("")

    suggestions.append(f"测试概况: {len(results)} 次执行 ({iterations} 轮 × {len(TEST_CASES)} 用例)")
    suggestions.append(f"模型: {os.environ['DEFAULT_MODEL']}")
    suggestions.append(f"平均总耗时: {analysis['summary']['avg']:.0f}ms")
    suggestions.append(f"P95 总耗时: {analysis['summary']['p95']:.0f}ms")
    suggestions.append(f"成功率: {analysis['summary']['overall_success_rate']:.0%}")
    suggestions.append("")

    suggestions.append("## 耗时分布")
    for key in ("llm_calls", "tool_calls", "overhead"):
        b = breakdown[key]
        suggestions.append(f"  {key}:  avg={b['avg']:.0f}ms  p95={b['p95']:.0f}ms  占比={b['percent']:.1f}%")
    suggestions.append("")

    # 针对性建议
    llm_pct = breakdown["llm_calls"]["percent"]
    overhead_pct = breakdown["overhead"]["percent"]
    tool_pct = breakdown["tool_calls"]["percent"]

    if llm_pct > 50:
        suggestions.append(f"[WARN] 瓶颈: LLM 推理占 {llm_pct:.0f}%")
        suggestions.append(f"  -> 当前 avg={breakdown['llm_calls']['avg']:.0f}ms/次调用")
        suggestions.append("  -> 建议: 1) 使用更小的模型 (如 2B) 2) 检查 LMStudio GPU 加速是否启用")
        suggestions.append("  -> 建议: 3) 减小 MAX_TOKENS 限制 4) 检查 prompt 长度是否可压缩")
        suggestions.append("")

    if overhead_pct > 40:
        suggestions.append(f"[WARN] 瓶颈: 系统框架开销占 {overhead_pct:.0f}%")
        suggestions.append("  -> 开销包括: 意图分析、prompt 编译、记忆检索、会话管理")
        suggestions.append("  -> 建议: 1) 降低 RETRIEVAL_TOP_K (当前默认5)")
        suggestions.append("  -> 建议: 2) 调整 MEMORY_SIMILARITY_THRESHOLD")
        suggestions.append("  -> 建议: 3) 检查 memory DB 索引是否优化")
        suggestions.append("")

    if tool_pct > 30:
        suggestions.append(f"[WARN] 瓶颈: 工具调用占 {tool_pct:.0f}%")
        suggestions.append("  -> 建议: 1) 检查工具超时设置")
        suggestions.append("  -> 建议: 2) 评估是否可减少工具调用轮数")
        suggestions.append("")

    # 最慢用例
    slowest = analysis["slowest_tests"]
    if slowest:
        suggestions.append("## 最慢的测试用例")
        for t in slowest[:3]:
            suggestions.append(f"  {t['test_id']}: avg={t['avg_total_ms']:.0f}ms  p95={t['p95_total_ms']:.0f}ms  llm={t['avg_llm_ms']:.0f}ms  overhead={t['avg_overhead_ms']:.0f}ms")
        suggestions.append("")

    # 自进化集成建议
    suggestions.append("## 可与自进化系统联动优化")
    suggestions.append("  1. 将 profile 输出作为 benchmark 任务的新指标维度")
    suggestions.append("  2. 如果 LLM 占比较高 -> 让实验循环尝试调整 BENCHMARK_TASK_TIMEOUT")
    suggestions.append("  3. 如果 overhead 较高 -> 触发 _get_memory_tuning_hint 自动建议参数调整")
    suggestions.append("  4. 定期运行 profile 对比，验证自进化是否真正降低了延迟")

    (output_dir / "optimization_suggestions.txt").write_text(
        "\n".join(suggestions), encoding="utf-8",
    )
    print(f"  [OK] optimization_suggestions.txt")


def _percentile(sorted_data: list[float], p: float) -> float:
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    d0 = sorted_data[f] * (c - k)
    d1 = sorted_data[c] * (k - f)
    return d0 + d1


# ── 对比模式 ──────────────────────────────────────────────────
def compare_with_baseline(current: list[TestResult], baseline_path: Path) -> None:
    if not baseline_path.exists():
        print(f"  [WARN] 基准文件不存在: {baseline_path}")
        return
    baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(baseline_data, list):
        print("  [WARN] 基准文件格式不正确")
        return

    bl_map: dict[str, float] = {}
    for item in baseline_data:
        tid = item.get("test_id", "")
        bl_map.setdefault(tid, []).append(item.get("total_ms", 0))

    bl_avg: dict[str, float] = {k: statistics.mean(v) for k, v in bl_map.items() if v}

    cur_map: dict[str, list[float]] = {}
    for r in current:
        cur_map.setdefault(r.test_id, []).append(r.total_ms)

    print("\n  ── 基准对比 ──")
    improved = 0
    degraded = 0
    for tid in sorted(cur_map):
        cur_avg = statistics.mean(cur_map[tid])
        bl = bl_avg.get(tid, cur_avg)
        delta_pct = (cur_avg - bl) / max(bl, 1) * 100
        direction = "↓ 改善" if delta_pct < -5 else ("up 退化" if delta_pct > 5 else "-> 持平")
        if delta_pct < -5:
            improved += 1
        elif delta_pct > 5:
            degraded += 1
        print(f"  {tid}: {bl:.0f}ms -> {cur_avg:.0f}ms ({delta_pct:+.1f}%) {direction}")
    print(f"  改善 {improved} 项, 退化 {degraded} 项")


# ── 主入口 ────────────────────────────────────────────────────
async def amain(args):
    # 验证 LMStudio 连接
    print(f"模型: {os.environ['DEFAULT_MODEL']}")
    print(f"端点: {os.environ['OPENAI_BASE_URL']}")
    print(f"迭代: {args.iterations} 次")
    print(f"输出: {args.output}")
    print()

    # 初始化
    print("[1/4] 初始化 Agent ...")
    pa = ProfilingAgent()
    try:
        await pa.warmup()
    except Exception as e:
        print(f"  [WARN] 预热失败: {e} (继续运行)")

    # 运行测试
    print(f"[2/4] 运行 {len(TEST_CASES)} 个测试用例 × {args.iterations} 轮 ...")
    all_results: list[TestResult] = []

    for iteration in range(args.iterations):
        print(f"\n  ── 第 {iteration + 1}/{args.iterations} 轮 ──")
        for i, test in enumerate(TEST_CASES):
            tid = test["id"]
            conv_id = f"profile_{tid}_{iteration}_{int(time.time())}"
            mid = "[...]"
            try:
                result = await pa.run_one(test, iteration + 1, conv_id)
                mid = "[PASS]" if result.success else "[FAIL]"
                print(
                    f"  {mid} {tid:20s}  {result.total_ms:8.0f}ms  "
                    f"LLMx{len(result.llm_calls):<2d}  toolsx{len(result.tool_calls):<2d}  "
                    f"tokens:{result.tokens_output:>5d}"
                    + (f"  ERR: {result.error[:60]}" if result.error else "")
                )
            except Exception as e:
                mid = "[CRASH]"
                print(f"  {mid} {tid:20s}  CRASH: {type(e).__name__}: {e}")
                result = TestResult(
                    test_id=tid, category=test.get("category", ""),
                    user_input=test["input"][:200], iteration=iteration + 1,
                    error=f"{type(e).__name__}: {e}",
                )
            all_results.append(result)

    # 生成报告
    print(f"\n[3/4] 生成报告 ...")
    output_dir = Path(args.output)
    build_report(all_results, args.iterations, output_dir)

    # 对比基准
    if args.compare:
        print(f"\n[4/4] 对比基准 ...")
        compare_with_baseline(all_results, Path(args.compare))

    # 关闭
    await pa.close()

    # 统计
    successes = sum(1 for r in all_results if r.success)
    print(f"\n  总计: {len(all_results)} 次, 成功: {successes}")

    return 0


def main():
    parser = argparse.ArgumentParser(description="OpenAkita 全链路性能剖析脚本")
    parser.add_argument("--iterations", type=int, default=2, help="每个用例重复次数 (默认 2)")
    parser.add_argument("--output", type=str, default="reports/", help="报告输出目录")
    parser.add_argument("--compare", type=str, default="", help="与基准报告对比 (路径)")
    args = parser.parse_args()

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
