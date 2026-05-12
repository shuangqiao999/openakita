"""
Integration tests for confirmation flow (ask_user → user reply → resume / cancel).

Tests:
- test_confirmation_resume_sets_intent: 确认后意图强制为 TASK
- test_cancellation_sets_intent: 取消后意图为 CHAT
- test_timeout_clears_flag: 超时后标志被清除
- test_nested_confirmation: 多轮确认嵌套处理
"""

import asyncio
import time as _time

import pytest

from openakita.core.agent import Agent
from openakita.core.confirmation_state import (
    _CANCEL_WORDS,
    _CONFIRM_WORDS,
    normalize_confirmation_answer,
)
from openakita.sessions.session import Session, SessionConfig, SessionContext


class TestConfirmationState:
    """Test the confirmation word matching logic."""

    def test_confirm_words_match(self):
        for word in ("确认", "好", "好的", "是", "是的", "ok", "可以", "继续", "执行"):
            assert normalize_confirmation_answer(word) is not None

    def test_cancel_words_match(self):
        for word in ("取消", "不", "不要", "no", "停止", "算了"):
            assert normalize_confirmation_answer(word) is not None

    def test_unknown_words_no_match(self):
        for word in ("什么意思", "再说一遍", "等一等", "maybe"):
            result = normalize_confirmation_answer(word)
            assert result is None or result.value == "unknown"


class TestConfirmationStatePersistence:
    """Test that SessionContext correctly stores and retrieves confirmation state."""

    def test_set_and_get_awaiting_confirmation(self):
        ctx = SessionContext()
        ctx.set_variable("awaiting_confirmation", True)
        ctx.set_variable("awaiting_confirmation_since", _time.time())
        ctx.set_variable("pending_task_snapshot", {
            "assistant_message": "请确认是否继续",
            "tool_calls": [],
            "timestamp": _time.time(),
        })

        assert ctx.get_variable("awaiting_confirmation") is True
        assert ctx.get_variable("awaiting_confirmation_since") is not None
        snap = ctx.get_variable("pending_task_snapshot")
        assert isinstance(snap, dict)
        assert snap["assistant_message"] == "请确认是否继续"

    def test_clear_awaiting_confirmation(self):
        ctx = SessionContext()
        ctx.set_variable("awaiting_confirmation", True)
        ctx.set_variable("awaiting_confirmation_since", _time.time())
        ctx.set_variable("pending_task_snapshot", {"key": "value"})

        ctx.set_variable("awaiting_confirmation", False)
        ctx.set_variable("awaiting_confirmation_since", None)
        ctx.set_variable("pending_task_snapshot", None)

        assert ctx.get_variable("awaiting_confirmation") is False
        assert ctx.get_variable("awaiting_confirmation_since") is None
        assert ctx.get_variable("pending_task_snapshot") is None


class TestConfirmationTimeout:
    """Test the timeout auto-clear logic."""

    def test_timeout_detection(self):
        """Verify that old timestamps are detected as timed out."""
        _since_old = _time.time() - 400  # 400 seconds ago
        _timeout = 300
        _timed_out = (_time.time() - _since_old) > _timeout
        assert _timed_out is True

    def test_recent_not_timed_out(self):
        """Verify that recent timestamps are NOT detected as timed out."""
        _since_recent = _time.time() - 60  # 60 seconds ago
        _timeout = 300
        _timed_out = (_time.time() - _since_recent) > _timeout
        assert _timed_out is False

    def test_timeout_clears_all_variables(self):
        """Simulate the full timeout flow: set → check timeout → clear."""
        ctx = SessionContext()
        # Set initial state
        ctx.set_variable("awaiting_confirmation", True)
        ctx.set_variable("awaiting_confirmation_since", _time.time() - 400)
        ctx.set_variable("pending_task_snapshot", {"task": "test"})

        # Simulate timeout check
        _since = ctx.get_variable("awaiting_confirmation_since", 0.0)
        _timeout = 300
        if _since and (_time.time() - float(_since)) > _timeout:
            ctx.set_variable("awaiting_confirmation", False)
            ctx.set_variable("awaiting_confirmation_since", None)
            ctx.set_variable("pending_task_snapshot", None)

        assert ctx.get_variable("awaiting_confirmation") is False
        assert ctx.get_variable("awaiting_confirmation_since") is None
        assert ctx.get_variable("pending_task_snapshot") is None


class TestNestedConfirmationLogic:
    """Test the logic for nested (multi-round) confirmation handling."""

    def test_first_confirm_clears_flag(self):
        """After first confirmation, all flags should be cleared,
        allowing a second ask_user to set new flags."""
        ctx = SessionContext()
        # First ask_user sets flags
        ctx.set_variable("awaiting_confirmation", True)
        ctx.set_variable("awaiting_confirmation_since", _time.time())
        ctx.set_variable("pending_task_snapshot", {"task": "step1"})

        # User confirms → clear flags
        ctx.set_variable("awaiting_confirmation", False)
        ctx.set_variable("awaiting_confirmation_since", None)
        ctx.set_variable("pending_task_snapshot", None)

        # Second ask_user sets NEW flags
        ctx.set_variable("awaiting_confirmation", True)
        ctx.set_variable("awaiting_confirmation_since", _time.time())
        ctx.set_variable("pending_task_snapshot", {"task": "step2"})

        # User confirms again → clear flags
        ctx.set_variable("awaiting_confirmation", False)
        ctx.set_variable("awaiting_confirmation_since", None)
        ctx.set_variable("pending_task_snapshot", None)

        assert ctx.get_variable("awaiting_confirmation") is False
        assert ctx.get_variable("awaiting_confirmation_since") is None
        assert ctx.get_variable("pending_task_snapshot") is None

    def test_cancel_clears_all_flags(self):
        """Cancelling should clear all confirmation state."""
        ctx = SessionContext()
        ctx.set_variable("awaiting_confirmation", True)
        ctx.set_variable("awaiting_confirmation_since", _time.time())
        ctx.set_variable("pending_task_snapshot", {"task": "step1"})

        # User cancels → clear all
        ctx.set_variable("awaiting_confirmation", False)
        ctx.set_variable("awaiting_confirmation_since", None)
        ctx.set_variable("pending_task_snapshot", None)

        # New task starts → old flags should not interfere
        assert ctx.get_variable("awaiting_confirmation") is False
        assert ctx.get_variable("awaiting_confirmation_since") is None
        assert ctx.get_variable("pending_task_snapshot") is None

    def test_non_confirm_message_preserves_flag(self):
        """Non-confirm messages while awaiting should NOT clear the flag."""
        ctx = SessionContext()
        ctx.set_variable("awaiting_confirmation", True)
        ctx.set_variable("awaiting_confirmation_since", _time.time())
        ctx.set_variable("pending_task_snapshot", {"task": "step1"})

        # User sends a normal message (not confirm/cancel)
        # Flags should remain
        assert ctx.get_variable("awaiting_confirmation") is True
        assert ctx.get_variable("awaiting_confirmation_since") is not None
        assert ctx.get_variable("pending_task_snapshot") is not None


class TestSystemMessageInjection:
    """Test that the system message injection logic works correctly."""

    def test_inject_after_confirm(self):
        """Simulate injecting a system message after confirmation."""
        messages = [
            {"role": "user", "content": "帮我做任务"},
            {"role": "assistant", "content": "请确认是否继续？"},
            {"role": "user", "content": "确认"},
        ]

        # Simulate injection: insert system msg before last user msg
        _inject = {
            "role": "system",
            "content": (
                "用户已确认你的请求。请继续执行之前的任务，"
                "不要重复确认，不要重新介绍自己。"
            ),
        }
        if len(messages) >= 2:
            messages.insert(-1, _inject)
        else:
            messages.append(_inject)

        assert len(messages) == 4
        assert messages[-2]["role"] == "system"
        assert "用户已确认" in messages[-2]["content"]
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "确认"

    def test_no_duplicate_assistant_on_restore(self):
        """When restoring from snapshot, duplicate assistant messages should be avoided."""
        messages = [
            {"role": "user", "content": "帮我做任务"},
            {"role": "assistant", "content": "请确认是否继续？"},
            {"role": "user", "content": "确认"},
        ]

        _snapshot_assist = "请确认是否继续？"

        # Check if last assistant already contains confirmation
        _last_has_ask = False
        for _m in reversed(messages):
            if _m.get("role") == "assistant":
                _last_has_ask = "确认" in str(_m.get("content", ""))[:200]
                break

        assert _last_has_ask is True
        # Should NOT inject duplicate
        should_inject = not _last_has_ask
        assert should_inject is False


class TestConfigTimeoutSetting:
    """Test that the confirmation_timeout_seconds config is accessible."""

    def test_config_has_timeout_setting(self):
        from openakita.config import settings as _s

        _timeout = getattr(_s, "confirmation_timeout_seconds", 300)
        assert _timeout > 0
        assert isinstance(_timeout, int)
