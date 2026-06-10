"""
OpenAkita 自我进化模块
"""

from .analyzer import NeedAnalyzer
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
