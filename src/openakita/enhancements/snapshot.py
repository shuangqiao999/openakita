"""
自愈能力 - 状态快照与断点续传机制

保存时机：
- 每个任务步骤完成后
- 军人Agent被外部暂停时
- 系统收到关闭信号时
- 定期每30秒自动保存

恢复流程：
- 军人Agent重启后，检查是否有未完成的任务快照
- 如有，从快照中恢复执行进度
- 继续执行下一步，不重复已完成步骤
"""

import asyncio
import json
import logging
import pickle
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..config import settings

logger = logging.getLogger(__name__)


@dataclass
class StateSnapshot:
    """状态快照"""

    snapshot_id: str
    mission_id: str
    task_id: str
    soldier_id: str
    created_at: datetime = field(default_factory=datetime.now)

    # 执行进度
    current_step: int = 0
    total_steps: int = 0
    completed_steps: list[int] = field(default_factory=list)

    # 中间结果
    intermediate_results: dict[int, Any] = field(default_factory=dict)

    # 上下文变量
    context_vars: dict[str, Any] = field(default_factory=dict)

    # 原始请求
    original_request: str = ""
    original_parameters: dict[str, Any] = field(default_factory=dict)

    # 版本信息
    version: int = 1

    @property
    def is_complete(self) -> bool:
        """是否已完成"""
        return self.current_step >= self.total_steps and self.total_steps > 0

    @property
    def progress(self) -> float:
        """进度（0.0-1.0）"""
        if self.total_steps == 0:
            return 0.0
        return min(1.0, len(self.completed_steps) / self.total_steps)


class SnapshotManager:
    """状态快照管理器"""

    def __init__(
        self,
        snapshot_dir: Path | None = None,
        auto_save_interval_seconds: int = 30,
    ):
        self.snapshot_dir = snapshot_dir or (
            settings.project_root / "data" / "snapshots"
        )
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

        self.auto_save_interval = auto_save_interval_seconds
        self._auto_save_task: asyncio.Task | None = None
        self._snapshots: dict[str, StateSnapshot] = {}
        self._running = False

    async def start(self) -> None:
        """启动快照管理器（包括自动保存）"""
        if self._running:
            logger.warning("SnapshotManager already running")
            return

        self._running = True
        self._auto_save_task = asyncio.create_task(self._auto_save_loop())
        logger.info("SnapshotManager started")

    async def stop(self) -> None:
        """停止快照管理器"""
        self._running = False

        if self._auto_save_task:
            self._auto_save_task.cancel()
            try:
                await self._auto_save_task
            except asyncio.CancelledError:
                pass

        # 保存所有未完成的快照
        for snapshot in self._snapshots.values():
            if not snapshot.is_complete:
                await self._save_snapshot_to_disk(snapshot)

        logger.info("SnapshotManager stopped")

    def create_snapshot(
        self,
        mission_id: str,
        task_id: str,
        soldier_id: str,
        total_steps: int = 0,
        original_request: str = "",
        original_parameters: dict[str, Any] | None = None,
    ) -> StateSnapshot:
        """创建新的状态快照"""
        import uuid

        snapshot = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            mission_id=mission_id,
            task_id=task_id,
            soldier_id=soldier_id,
            total_steps=total_steps,
            original_request=original_request,
            original_parameters=original_parameters or {},
        )

        self._snapshots[snapshot.snapshot_id] = snapshot
        logger.debug(f"Created snapshot: {snapshot.snapshot_id}")
        return snapshot

    def get_snapshot(self, snapshot_id: str) -> StateSnapshot | None:
        """获取快照"""
        return self._snapshots.get(snapshot_id)

    def get_snapshots_for_mission(self, mission_id: str) -> list[StateSnapshot]:
        """获取任务的所有快照"""
        return [s for s in self._snapshots.values() if s.mission_id == mission_id]

    def get_latest_unfinished_for_soldier(
        self, soldier_id: str
    ) -> StateSnapshot | None:
        """获取军人最近的未完成快照"""
        soldier_snapshots = [
            s
            for s in self._snapshots.values()
            if s.soldier_id == soldier_id and not s.is_complete
        ]

        if not soldier_snapshots:
            return None

        return max(soldier_snapshots, key=lambda s: s.created_at)

    def update_step(
        self,
        snapshot_id: str,
        step_number: int,
        result: Any = None,
    ) -> bool:
        """更新步骤进度"""
        snapshot = self._snapshots.get(snapshot_id)
        if not snapshot:
            logger.warning(f"Snapshot not found: {snapshot_id}")
            return False

        snapshot.current_step = step_number
        if step_number not in snapshot.completed_steps:
            snapshot.completed_steps.append(step_number)
        if result is not None:
            snapshot.intermediate_results[step_number] = result

        return True

    def update_context(
        self, snapshot_id: str, key: str, value: Any
    ) -> bool:
        """更新上下文变量"""
        snapshot = self._snapshots.get(snapshot_id)
        if not snapshot:
            return False

        snapshot.context_vars[key] = value
        return True

    def mark_complete(self, snapshot_id: str) -> bool:
        """标记快照为完成"""
        snapshot = self._snapshots.get(snapshot_id)
        if not snapshot:
            return False

        snapshot.current_step = snapshot.total_steps
        logger.info(f"Snapshot marked complete: {snapshot_id}")
        return True

    async def save_snapshot(self, snapshot_id: str) -> bool:
        """保存快照到磁盘"""
        snapshot = self._snapshots.get(snapshot_id)
        if not snapshot:
            return False

        return await self._save_snapshot_to_disk(snapshot)

    async def _save_snapshot_to_disk(self, snapshot: StateSnapshot) -> bool:
        """保存快照到磁盘"""
        try:
            snapshot_file = (
                self.snapshot_dir
                / f"{snapshot.mission_id}_{snapshot.task_id}_{snapshot.snapshot_id}.json"
            )

            # 序列化
            data = {
                "snapshot_id": snapshot.snapshot_id,
                "mission_id": snapshot.mission_id,
                "task_id": snapshot.task_id,
                "soldier_id": snapshot.soldier_id,
                "created_at": snapshot.created_at.isoformat(),
                "current_step": snapshot.current_step,
                "total_steps": snapshot.total_steps,
                "completed_steps": snapshot.completed_steps,
                "intermediate_results": self._serialize_for_json(
                    snapshot.intermediate_results
                ),
                "context_vars": self._serialize_for_json(snapshot.context_vars),
                "original_request": snapshot.original_request,
                "original_parameters": self._serialize_for_json(
                    snapshot.original_parameters
                ),
                "version": snapshot.version,
            }

            with open(snapshot_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.debug(f"Saved snapshot to disk: {snapshot_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to save snapshot: {e}", exc_info=True)
            return False

    async def load_snapshot(self, snapshot_id: str) -> StateSnapshot | None:
        """从磁盘加载快照"""
        try:
            # 先查找内存中的
            if snapshot_id in self._snapshots:
                return self._snapshots[snapshot_id]

            # 从磁盘查找
            snapshot_files = list(self.snapshot_dir.glob(f"*_{snapshot_id}.json"))
            if not snapshot_files:
                return None

            snapshot_file = snapshot_files[0]

            with open(snapshot_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            snapshot = StateSnapshot(
                snapshot_id=data["snapshot_id"],
                mission_id=data["mission_id"],
                task_id=data["task_id"],
                soldier_id=data["soldier_id"],
                created_at=datetime.fromisoformat(data["created_at"]),
                current_step=data["current_step"],
                total_steps=data["total_steps"],
                completed_steps=data["completed_steps"],
                intermediate_results=self._deserialize_from_json(
                    data["intermediate_results"]
                ),
                context_vars=self._deserialize_from_json(data["context_vars"]),
                original_request=data["original_request"],
                original_parameters=self._deserialize_from_json(
                    data["original_parameters"]
                ),
                version=data["version"],
            )

            self._snapshots[snapshot_id] = snapshot
            logger.debug(f"Loaded snapshot from disk: {snapshot_file}")
            return snapshot

        except Exception as e:
            logger.error(f"Failed to load snapshot: {e}", exc_info=True)
            return None

    def _serialize_for_json(self, obj: Any) -> Any:
        """序列化对象为JSON兼容格式"""
        try:
            json.dumps(obj)
            return obj
        except (TypeError, OverflowError):
            return str(obj)

    def _deserialize_from_json(self, obj: Any) -> Any:
        """从JSON反序列化"""
        # 简单版本，实际可能需要更复杂的处理
        return obj

    async def _auto_save_loop(self) -> None:
        """自动保存循环"""
        while self._running:
            try:
                # 保存所有未完成的快照
                for snapshot in self._snapshots.values():
                    if not snapshot.is_complete:
                        await self._save_snapshot_to_disk(snapshot)

                await asyncio.sleep(self.auto_save_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Auto-save loop error", exc_info=True)
                await asyncio.sleep(5)

    def cleanup_old_snapshots(self, keep_hours: int = 24) -> int:
        """清理旧快照"""
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(hours=keep_hours)
        cleaned_count = 0

        for snapshot_file in list(self.snapshot_dir.glob("*.json")):
            try:
                mtime = datetime.fromtimestamp(snapshot_file.stat().st_mtime)
                if mtime < cutoff:
                    snapshot_file.unlink()
                    cleaned_count += 1
            except Exception as e:
                logger.warning(f"Failed to cleanup snapshot {snapshot_file}: {e}")

        # 同时清理内存中的
        for snapshot_id in list(self._snapshots.keys()):
            if self._snapshots[snapshot_id].created_at < cutoff:
                del self._snapshots[snapshot_id]

        logger.info(f"Cleaned up {cleaned_count} old snapshots")
        return cleaned_count
