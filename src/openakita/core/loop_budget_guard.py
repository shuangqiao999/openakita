"""Shared hard-budget guard for ReAct loops."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


READONLY_EXPLORATION_TOOLS = frozenset({
    "read_file",
    "list_directory",
    "grep",
    "glob",
    "get_tool_info",
    "list_skills",
    "get_skill_info",
    "search_memory",
    "get_memory_stats",
    "get_session_context",
})


@dataclass(frozen=True)
class LoopBudgetDecision:
    should_stop: bool
    exit_reason: str = ""
    message: str = ""


@dataclass
class LoopBudgetGuard:
    max_total_tool_calls: int = 30
    readonly_stagnation_limit: int = 3
    token_anomaly_threshold: int = 200_000
    total_tool_calls_seen: int = 0
    readonly_seen_fingerprints: set[str] = field(default_factory=set)
    readonly_stagnation_rounds: int = 0

    def record_tool_calls(self, tool_calls: list[dict]) -> LoopBudgetDecision:
        self.total_tool_calls_seen += len(tool_calls or [])
        if self.total_tool_calls_seen > self.max_total_tool_calls:
            return LoopBudgetDecision(
                True,
                "tool_budget_exceeded",
                f"⚠️ 本轮任务工具调用已达到预算上限（{self.max_total_tool_calls} 次），"
                "已自动终止以避免继续消耗 token。请基于已有结果给出结论，"
                "或缩小范围后重新发起。",
            )
        return LoopBudgetDecision(False)

    def record_tool_results(
        self,
        tool_calls: list[dict],
        tool_results: list[dict],
    ) -> LoopBudgetDecision:
        if self._is_readonly_exploration_round(tool_calls):
            fingerprint = self._tool_result_fingerprint(tool_results)
            if not fingerprint or fingerprint in self.readonly_seen_fingerprints:
                self.readonly_stagnation_rounds += 1
            else:
                self.readonly_seen_fingerprints.add(fingerprint)
                self.readonly_stagnation_rounds = 0
            if self.readonly_stagnation_rounds >= self.readonly_stagnation_limit:
                return LoopBudgetDecision(
                    True,
                    "readonly_stagnation",
                    "⚠️ 只读探索已经连续多轮没有获得新信息，任务已自动终止。"
                    "请基于已经读取到的内容总结结论，或提供更具体的文件/关键词继续。",
                )
        else:
            self.readonly_stagnation_rounds = 0
        return LoopBudgetDecision(False)

    def check_token_growth(self, input_tokens: int, output_tokens: int) -> LoopBudgetDecision:
        if (
            input_tokens + output_tokens > self.token_anomaly_threshold
            and self.total_tool_calls_seen >= max(5, self.max_total_tool_calls // 2)
        ):
            return LoopBudgetDecision(
                True,
                "token_growth_terminated",
                "⚠️ 检测到上下文 token 异常膨胀且工具调用已接近预算，"
                "已自动终止以避免继续扩大上下文。请基于已有信息总结结论。",
            )
        return LoopBudgetDecision(False)

    @staticmethod
    def _tool_result_fingerprint(tool_results: list[dict]) -> str:
        parts: list[str] = []
        for result in tool_results:
            if not isinstance(result, dict):
                continue
            content = str(result.get("content", ""))
            parts.append(hashlib.md5(content[:4000].encode("utf-8", errors="ignore")).hexdigest()[:10])
        return "|".join(parts)

    @staticmethod
    def _is_readonly_exploration_round(tool_calls: list[dict]) -> bool:
        if not tool_calls:
            return False
        names = {str(tc.get("name", "")) for tc in tool_calls if isinstance(tc, dict)}
        return bool(names) and names.issubset(READONLY_EXPLORATION_TOOLS)
