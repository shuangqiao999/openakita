"""
Test the generic 'continue' word detection logic.

Verifies that bare continuation words like "继续" are NOT treated as new
conversation starts, even when the formal awaiting_confirmation flag is False.
"""

import pytest
from openakita.core.agent import _CONTINUE_WORDS, _CONTINUE_MINIMUM_RECENT_TURNS


class TestContinueWords:
    """Verify the _CONTINUE_WORDS constant is correctly defined."""

    def test_continue_words_is_frozenset(self):
        assert isinstance(_CONTINUE_WORDS, frozenset)

    def test_chinese_continue_words_present(self):
        expected = {"继续", "接着", "继续吧", "继续执行", "接着说", "继续做", "继续完成", "继续处理", "接着做", "继续下一步"}
        assert expected.issubset(_CONTINUE_WORDS), (
            f"Missing Chinese continue words: {expected - _CONTINUE_WORDS}"
        )

    def test_english_continue_words_present(self):
        expected = {"go on", "continue", "go ahead", "proceed", "keep going"}
        assert expected.issubset(_CONTINUE_WORDS), (
            f"Missing English continue words: {expected - _CONTINUE_WORDS}"
        )

    def test_all_words_are_lowercase(self):
        """All English words must be lowercase for case-insensitive matching."""
        for w in _CONTINUE_WORDS:
            assert w == w.lower(), f"Word '{w}' is not lowercase"

    def test_continue_words_are_trimmed(self):
        """No whitespace-only entries."""
        for w in _CONTINUE_WORDS:
            assert w.strip() == w, f"Word '{w}' has leading/trailing whitespace"

    @pytest.mark.parametrize("word", [
        "继续", "接着", "继续吧", "继续执行", "接着说", "continue",
    ])
    def test_common_continue_inputs_match(self, word):
        """Realistic user inputs should match."""
        assert word.strip().lower() in _CONTINUE_WORDS


class TestContinueNotFalsePositive:
    """Verify that normal messages are NOT treated as continuation requests."""

    @pytest.mark.parametrize("word", [
        "你好", "hello", "帮我写个脚本", "今天天气怎么样",
        "谢谢", "好的", "ok", "确认",
        "有什么可以帮到您的", "继续在哪个文件里",
        "不继续了", "能不能继续解释",  # "继续" as part of longer text
    ])
    def test_normal_messages_do_not_trigger_continue(self, word):
        """Normal conversation messages should NOT be in _CONTINUE_WORDS."""
        assert word.strip().lower() not in _CONTINUE_WORDS, (
            f"'{word}' should NOT be a continue word"
        )


class TestContinueMinimumTurns:
    """Verify the minimum history turns constant."""

    def test_minimum_turns_is_reasonable(self):
        """Need at least 2 turns (user+assistant) to have context to continue."""
        assert _CONTINUE_MINIMUM_RECENT_TURNS >= 2
        assert _CONTINUE_MINIMUM_RECENT_TURNS <= 10  # Not too aggressive
