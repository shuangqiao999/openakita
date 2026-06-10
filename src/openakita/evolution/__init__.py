"""
OpenAkita 自我进化模块
"""

import re


def strip_json_fences(text: str) -> str:
    """去除 LLM 返回中常见的 ```json ... ``` Markdown 代码栏及前后说明文字"""
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*\n?(.*?)```\s*$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    start = text.find("{")
    if start == -1:
        start = text.find("[")
    if start > 0:
        text = text[start:]
    end = text.rfind("}")
    if end == -1:
        end = text.rfind("]")
    if end >= 0 and end < len(text) - 1:
        text = text[: end + 1]
    return text


from .analyzer import NeedAnalyzer
from .approval_queue import ApprovalQueue
from .auto_evolve import AutoEvolver
from .benchmark import BenchmarkEngine
from .experiment_loop import ExperimentLoop
from .generator import SkillGenerator
from .installer import AutoInstaller
from .log_analyzer import ErrorPattern, LogAnalyzer, LogEntry
from .pattern_learner import PatternLearner
from .prompt_optimizer import PromptOptimizer
from .research_org import ResearchOrg
from .self_check import SelfChecker

__all__ = [
    "NeedAnalyzer",
    "ApprovalQueue",
    "AutoInstaller",
    "AutoEvolver",
    "BenchmarkEngine",
    "ExperimentLoop",
    "PromptOptimizer",
    "PatternLearner",
    "ResearchOrg",
    "SkillGenerator",
    "SelfChecker",
    "LogAnalyzer",
    "LogEntry",
    "ErrorPattern",
]
