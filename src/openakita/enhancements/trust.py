"""
渐进自动化 - 信任度评分与等级系统

让系统像人类实习生一样成长：
- 新任务需要人工确认
- 熟练后逐步放权
- 完全掌握后全自动执行

每个任务类型独立维护信任度。
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class TrustLevel(IntEnum):
    """信任等级（从低到高）"""

    OBSERVE = 0  # L0 观察模式 - 模拟执行，只显示预览
    CONFIRM = 1  # L1 需确认模式 - 关键步骤暂停等待确认
    SPOT_CHECK = 2  # L2 需抽查模式 - 随机抽查确认
    REPORT = 3  # L3 需汇报模式 - 执行完汇报，不暂停
    FULL_AUTO = 4  # L4 全自动模式 - 完全自主执行


class TrustAction(Enum):
    """信任度变化动作"""

    SUCCESS_NO_CORRECTION = "success_no_correction"
    SUCCESS_WITH_CORRECTION = "success_with_correction"
    USER_CANCEL_AUTO = "user_cancel_auto"
    FAILURE_AUTO_RECOVER = "failure_auto_recover"
    FAILURE_NEEDS_HUMAN = "failure_needs_human"
    USER_MARK_UNTRUSTED = "user_mark_untrusted"


@dataclass
class TrustScore:
    """信任度分数记录"""

    task_type_id: str
    task_type_name: str | None = None
    score: int = 0
    level: TrustLevel = TrustLevel.OBSERVE
    success_count: int = 0
    total_count: int = 0
    history: list[tuple[datetime, TrustAction, int, int]] = field(
        default_factory=list
    )  # (时间, 动作, 变化量, 新分数)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    is_locked: bool = False  # 是否被用户锁定等级
    locked_level: TrustLevel | None = None

    @property
    def current_level(self) -> TrustLevel:
        """获取当前有效等级（考虑锁定）"""
        if self.is_locked and self.locked_level is not None:
            return self.locked_level
        return self.level

    def apply_action(self, action: TrustAction) -> None:
        """应用信任度变化动作"""
        if self.is_locked:
            logger.debug(f"Trust score locked for {self.task_type_id}, skipping action")
            return

        old_score = self.score
        delta = self._get_delta_for_action(action)
        self.score = max(0, min(100, self.score + delta))
        self.total_count += 1

        if action in (
            TrustAction.SUCCESS_NO_CORRECTION,
            TrustAction.SUCCESS_WITH_CORRECTION,
        ):
            self.success_count += 1

        self.history.append((datetime.now(), action, delta, self.score))
        self.updated_at = datetime.now()

        # 自动调整等级
        self._update_level()

        logger.info(
            f"Trust score updated: {self.task_type_id} "
            f"{old_score} -> {self.score} (delta: {delta}), "
            f"level: {self.current_level.name}"
        )

    def _get_delta_for_action(self, action: TrustAction) -> int:
        """获取动作对应的分数变化量"""
        delta_map = {
            TrustAction.SUCCESS_NO_CORRECTION: 10,
            TrustAction.SUCCESS_WITH_CORRECTION: 0,
            TrustAction.USER_CANCEL_AUTO: -20,
            TrustAction.FAILURE_AUTO_RECOVER: -5,
            TrustAction.FAILURE_NEEDS_HUMAN: -30,
            TrustAction.USER_MARK_UNTRUSTED: -50,
        }
        return delta_map.get(action, 0)

    def _update_level(self) -> None:
        """根据分数更新等级"""
        old_level = self.level

        if self.score >= 95:
            self.level = TrustLevel.FULL_AUTO
        elif self.score >= 80:
            self.level = TrustLevel.REPORT
        elif self.score >= 60:
            self.level = TrustLevel.SPOT_CHECK
        elif self.success_count >= 3:
            self.level = TrustLevel.CONFIRM
        else:
            self.level = TrustLevel.OBSERVE

        if self.level != old_level:
            logger.info(
                f"Trust level changed: {self.task_type_id} "
                f"{old_level.name} -> {self.level.name}"
            )

    def lock_level(self, level: TrustLevel) -> None:
        """锁定等级"""
        self.is_locked = True
        self.locked_level = level
        self.updated_at = datetime.now()
        logger.info(f"Trust level locked: {self.task_type_id} -> {level.name}")

    def unlock_level(self) -> None:
        """解锁等级"""
        self.is_locked = False
        self.locked_level = None
        self.updated_at = datetime.now()
        logger.info(f"Trust level unlocked: {self.task_type_id}")


@dataclass
class TaskType:
    """任务类型"""

    type_id: str
    name: str
    keywords: list[str] = field(default_factory=list)
    semantic_vector: list[float] | None = None
    user_defined: bool = False  # 是否用户手动标记


class TrustManager:
    """信任度管理器"""

    # 等级阈值
    L0_TO_L1_SUCCESS_COUNT = 3
    L1_TO_L2_SCORE = 60
    L2_TO_L3_SCORE = 80
    L3_TO_L4_SCORE = 95

    # L2 抽查概率
    INITIAL_SPOT_CHECK_RATE = 0.3  # 30%
    MIN_SPOT_CHECK_RATE = 0.05  # 5%

    def __init__(self):
        self._trust_scores: dict[str, TrustScore] = {}
        self._task_types: dict[str, TaskType] = {}
        self._spot_check_rate: float = self.INITIAL_SPOT_CHECK_RATE

    def register_task_type(
        self,
        type_id: str,
        name: str,
        keywords: list[str] | None = None,
        user_defined: bool = False,
    ) -> TaskType:
        """注册任务类型"""
        task_type = TaskType(
            type_id=type_id,
            name=name,
            keywords=keywords or [],
            user_defined=user_defined,
        )
        self._task_types[type_id] = task_type

        # 初始化信任度（如果不存在）
        if type_id not in self._trust_scores:
            self._trust_scores[type_id] = TrustScore(
                task_type_id=type_id,
                task_type_name=name,
            )

        return task_type

    def get_or_create_trust_score(self, task_type_id: str) -> TrustScore:
        """获取或创建信任度"""
        if task_type_id not in self._trust_scores:
            self._trust_scores[task_type_id] = TrustScore(
                task_type_id=task_type_id,
            )
        return self._trust_scores[task_type_id]

    def get_trust_score(self, task_type_id: str) -> TrustScore | None:
        """获取信任度"""
        return self._trust_scores.get(task_type_id)

    def get_all_trust_scores(self) -> list[TrustScore]:
        """获取所有信任度"""
        return list(self._trust_scores.values())

    def record_action(
        self, task_type_id: str, action: TrustAction
    ) -> TrustScore:
        """记录信任度变化动作"""
        score = self.get_or_create_trust_score(task_type_id)
        score.apply_action(action)

        # 动态调整抽查概率
        self._update_spot_check_rate()

        return score

    def should_confirm(self, task_type_id: str) -> bool:
        """判断是否需要确认"""
        import random

        score = self.get_or_create_trust_score(task_type_id)
        level = score.current_level

        if level == TrustLevel.OBSERVE:
            return True
        elif level == TrustLevel.CONFIRM:
            return True
        elif level == TrustLevel.SPOT_CHECK:
            return random.random() < self._spot_check_rate
        elif level == TrustLevel.REPORT:
            return False
        elif level == TrustLevel.FULL_AUTO:
            return False

        return True

    def get_spot_check_probability(self, task_type_id: str) -> float:
        """获取抽查概率（考虑信任度）"""
        score = self.get_or_create_trust_score(task_type_id)
        # 信任度越高，抽查概率越低
        base_rate = self._spot_check_rate
        trust_factor = score.score / 100.0
        return max(self.MIN_SPOT_CHECK_RATE, base_rate * (1 - trust_factor))

    def _update_spot_check_rate(self) -> None:
        """根据平均信任度动态调整抽查概率"""
        if not self._trust_scores:
            return

        avg_score = sum(s.score for s in self._trust_scores.values()) / len(
            self._trust_scores
        )
        # 平均信任度越高，基础抽查概率越低
        self._spot_check_rate = max(
            self.MIN_SPOT_CHECK_RATE,
            self.INITIAL_SPOT_CHECK_RATE * (1 - avg_score / 100.0),
        )

    def lock_trust_level(self, task_type_id: str, level: TrustLevel) -> bool:
        """锁定信任等级"""
        score = self.get_trust_score(task_type_id)
        if score:
            score.lock_level(level)
            return True
        return False

    def unlock_trust_level(self, task_type_id: str) -> bool:
        """解锁信任等级"""
        score = self.get_trust_score(task_type_id)
        if score:
            score.unlock_level()
            return True
        return False

    def identify_task_type(
        self,
        request: str,
        user_provided_type: str | None = None,
    ) -> str:
        """
        识别任务类型

        优先级：
        1. 用户手动标记
        2. 语义相似度
        3. 关键词匹配
        """
        # 优先使用用户提供的类型
        if user_provided_type and user_provided_type in self._task_types:
            return user_provided_type

        # TODO: 实现语义相似度匹配（需要向量支持）

        # 关键词匹配（简单实现）
        request_lower = request.lower()
        for type_id, task_type in self._task_types.items():
            if task_type.user_defined:
                for keyword in task_type.keywords:
                    if keyword.lower() in request_lower:
                        return type_id

        # 默认类型
        return "default_general_task"
