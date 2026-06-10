"""
OpenAkita 自我进化模块
"""

import re


def strip_json_fences(text: str) -> str:
    """去除 LLM 返回中常见的 ```json ... ``` Markdown 代码栏"""
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*\n?(.*?)```\s*$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
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
