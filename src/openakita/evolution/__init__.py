"""
OpenAkita 自我进化模块
"""

from ._utils import strip_json_fences
from .analyzer import NeedAnalyzer
from .approval_queue import ApprovalQueue
from .auto_evolve import AutoEvolver
from .benchmark import BenchmarkEngine
from .conversation_quality import ConversationQualityEvaluator, QualityScore
from .dynamic_benchmark import DynamicBenchmarkGenerator
from .env_tuner import EnvTuner
from .experiment_loop import ExperimentLoop
from .generator import SkillGenerator
from .installer import AutoInstaller
from .log_analyzer import ErrorPattern, LogAnalyzer, LogEntry
from .pattern_learner import PatternLearner
from .research_org import ResearchOrg
from .runtime_metrics import RuntimeMetricsCollector, RuntimeSnapshot
from .self_check import SelfChecker

__all__ = [
    "strip_json_fences",
    "NeedAnalyzer",
    "ApprovalQueue",
    "AutoInstaller",
    "AutoEvolver",
    "BenchmarkEngine",
    "ConversationQualityEvaluator",
    "QualityScore",
    "DynamicBenchmarkGenerator",
    "EnvTuner",
    "ExperimentLoop",
    "PatternLearner",
    "ResearchOrg",
    "RuntimeMetricsCollector",
    "RuntimeSnapshot",
    "SkillGenerator",
    "SelfChecker",
    "LogAnalyzer",
    "LogEntry",
    "ErrorPattern",
]
