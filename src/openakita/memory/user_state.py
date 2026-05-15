"""
Persistent user pending-confirmation state machine.

SQLite-backed store for actions that require user confirmation
(e.g. delete_file, uninstall, destructive operations).

Unlike the in-memory PendingRiskConfirmationStore, this persists
across app restarts and session changes.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_CONFIRM_WORDS = frozenset({
    "confirm_continue", "继续", "确认", "继续吧", "继续执行",
    "好", "好的", "好滴", "好啊", "嗯", "嗯嗯",
    "是", "是的", "对", "对的",
    "行", "可以", "中", "ok", "okay", "yes", "y", "go", "gogogo",
    "同意", "批准", "通过", "执行", "开始", "开始吧", "做",
    "confirm", "continue",
})

_CANCEL_WORDS = frozenset({
    "cancel", "取消", "停止", "停", "否", "不", "不要", "不用",
    "no", "n", "nope", "abort", "skip", "跳过", "算了",
})


class UserConfirmationState:
    """Manage persistent pending confirmation actions per user."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def create_pending(
        self,
        user_id: str,
        tool_name: str,
        params: dict[str, Any],
        prompt: str,
        *,
        session_id: str = "",
    ) -> str:
        """Create a pending confirmation entry. Returns action_id."""
        action_id = f"upa_{uuid.uuid4().hex[:12]}"
        self._store.upsert_pending_action(
            user_id=user_id,
            action_id=action_id,
            tool_name=tool_name,
            params=params,
            prompt=prompt,
            session_id=session_id,
        )
        logger.info(
            f"[UserState] Created pending action {action_id}: "
            f"{tool_name} for user={user_id}"
        )
        return action_id

    def get_pending(self, user_id: str) -> list[dict]:
        """Get all pending actions for a user (newest first)."""
        return self._store.get_pending_actions(user_id)

    def resolve(
        self,
        user_id: str,
        message: str,
    ) -> dict | None:
        """Check if user message resolves a pending confirmation.

        Returns the pending action dict if user confirmed, or None
        if no match or user cancelled.
        Caller must check 'cancelled' key in the result.
        """
        normalized = (message or "").strip().lower()
        pending = self.get_pending(user_id)
        if not pending:
            return None

        latest = pending[0]
        action_id = latest.get("action_id", "")

        if normalized in _CONFIRM_WORDS:
            logger.info(
                f"[UserState] User confirmed pending action {action_id}"
            )
            self.delete(action_id)
            return {"action": latest, "cancelled": False}

        if normalized in _CANCEL_WORDS:
            logger.info(
                f"[UserState] User cancelled pending action {action_id}"
            )
            self.delete(action_id)
            return {"action": latest, "cancelled": True}

        # Unknown message — re-prompt
        return None

    def delete(self, action_id: str) -> bool:
        """Delete a specific pending action."""
        return self._store.delete_pending_action(action_id)

    def clear_user(self, user_id: str) -> int:
        """Delete all pending actions for a user."""
        return self._store.delete_pending_actions_for_user(user_id)
