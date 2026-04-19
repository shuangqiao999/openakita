"""
情报看板 (Dashboard) - 态势感知与人工介入

职责：
- 实时状态展示
- 异常告警
- 人工介入接口（暂停/恢复/取消/重派）
- 历史记录查询

禁止：
- 不决策
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .commander import Commander, MissionRecord
from .models import MissionStatus

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """告警信息"""

    alert_id: str
    level: str  # "info", "warning", "error", "critical"
    mission_id: str
    message: str
    created_at: datetime = field(default_factory=datetime.now)
    acknowledged: bool = False


class Dashboard:
    """
    情报看板 - 态势感知与人工介入接口
    """

    def __init__(self, commander: Commander):
        self.commander = commander
        self._alerts: list[Alert] = []
        self._alert_callbacks: list[Callable[[Alert], None]] = []
        self._status_callbacks: list[Callable[[MissionRecord], None]] = []
        self._event_history: list[dict[str, Any]] = []

        # 注册指挥官状态回调
        self.commander.register_status_callback(self._on_mission_status_change)

    def register_alert_callback(self, callback: Callable[[Alert], None]) -> None:
        """注册告警回调"""
        self._alert_callbacks.append(callback)

    def register_status_callback(self, callback: Callable[[MissionRecord], None]) -> None:
        """注册状态回调"""
        self._status_callbacks.append(callback)

    def _on_mission_status_change(self, record: MissionRecord) -> None:
        """任务状态变更处理"""
        # 记录事件
        self._event_history.append(
            {
                "timestamp": datetime.now().isoformat(),
                "mission_id": record.mission_id,
                "status": record.status.value,
            }
        )

        # 检查是否需要告警
        if record.status == MissionStatus.FAILED:
            self._raise_alert(
                level="error",
                mission_id=record.mission_id,
                message=f"Mission failed: {record.error}",
            )
        elif record.status == MissionStatus.WAITING_FOR_HUMAN:
            self._raise_alert(
                level="warning",
                mission_id=record.mission_id,
                message="Mission waiting for human intervention",
            )

        # 通知回调
        for callback in self._status_callbacks:
            try:
                callback(record)
            except Exception as e:
                logger.error(f"Status callback error: {e}", exc_info=True)

    def _raise_alert(self, level: str, mission_id: str, message: str) -> None:
        """触发告警"""
        import uuid

        alert = Alert(
            alert_id=str(uuid.uuid4()),
            level=level,
            mission_id=mission_id,
            message=message,
        )

        self._alerts.append(alert)
        logger.warning(f"Alert raised: [{level}] {mission_id} - {message}")

        # 通知回调
        for callback in self._alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.error(f"Alert callback error: {e}", exc_info=True)

    def get_all_missions(self) -> list[MissionRecord]:
        """获取所有任务"""
        return list(self.commander._missions.values())

    def get_mission(self, mission_id: str) -> MissionRecord | None:
        """获取单个任务"""
        return self.commander._missions.get(mission_id)

    def get_alerts(
        self, level: str | None = None, acknowledged: bool | None = None
    ) -> list[Alert]:
        """
        获取告警列表

        Args:
            level: 告警级别过滤
            acknowledged: 确认状态过滤

        Returns:
            告警列表
        """
        alerts = self._alerts

        if level is not None:
            alerts = [a for a in alerts if a.level == level]

        if acknowledged is not None:
            alerts = [a for a in alerts if a.acknowledged == acknowledged]

        return alerts

    def acknowledge_alert(self, alert_id: str) -> bool:
        """确认告警"""
        for alert in self._alerts:
            if alert.alert_id == alert_id:
                alert.acknowledged = True
                return True
        return False

    async def pause_mission(self, mission_id: str) -> bool:
        """
        暂停任务

        Args:
            mission_id: 任务 ID

        Returns:
            是否成功
        """
        try:
            return await self.commander.pause_mission(mission_id)
        except Exception as e:
            logger.error(f"Failed to pause mission: {e}", exc_info=True)
            return False

    async def resume_mission(self, mission_id: str) -> bool:
        """
        恢复任务

        Args:
            mission_id: 任务 ID

        Returns:
            是否成功
        """
        try:
            return await self.commander.resume_mission(mission_id)
        except Exception as e:
            logger.error(f"Failed to resume mission: {e}", exc_info=True)
            return False

    async def cancel_mission(self, mission_id: str) -> bool:
        """
        取消任务

        Args:
            mission_id: 任务 ID

        Returns:
            是否成功
        """
        try:
            await self.commander.cancel_mission(mission_id)
            return True
        except Exception as e:
            logger.error(f"Failed to cancel mission: {e}", exc_info=True)
            return False

    async def retry_mission(self, mission_id: str) -> bool:
        """
        重试任务

        Args:
            mission_id: 任务 ID

        Returns:
            是否成功
        """
        try:
            return await self.commander.retry_mission(mission_id)
        except Exception as e:
            logger.error(f"Failed to retry mission: {e}", exc_info=True)
            return False

    def get_event_history(
        self, mission_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """
        获取事件历史

        Args:
            mission_id: 任务 ID 过滤
            limit: 返回数量限制

        Returns:
            事件历史列表
        """
        history = self._event_history

        if mission_id is not None:
            history = [e for e in history if e.get("mission_id") == mission_id]

        return history[-limit:]

    def get_summary(self) -> dict[str, Any]:
        """
        获取整体态势摘要

        Returns:
            态势摘要
        """
        missions = list(self.commander._missions.values())

        status_counts = {}
        for status in MissionStatus:
            status_counts[status.value] = sum(
                1 for m in missions if m.status == status
            )

        return {
            "total_missions": len(missions),
            "status_counts": status_counts,
            "active_alerts": sum(1 for a in self._alerts if not a.acknowledged),
            "total_alerts": len(self._alerts),
            "timestamp": datetime.now().isoformat(),
        }
