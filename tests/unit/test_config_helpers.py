"""parse_bool 函数测试"""

import pytest
from openakita.config import parse_bool


def test_bool_values():
    assert parse_bool(True) is True
    assert parse_bool(False) is False


def test_int_float_values():
    assert parse_bool(1) is True
    assert parse_bool(0) is False
    assert parse_bool(100) is True
    assert parse_bool(-1) is False
    assert parse_bool(0.0) is False
    assert parse_bool(0.1) is True
    assert parse_bool(1.0) is True


def test_string_true_values():
    for v in ("true", "True", "TRUE", "1", "yes", "YES", "on", "ON", "enabled", "ENABLED", "enable"):
        assert parse_bool(v) is True, f"parse_bool({v!r}) != True"


def test_string_false_values():
    for v in ("false", "False", "FALSE", "0", "no", "NO", "off", "OFF", "disabled", "DISABLED", "disable"):
        assert parse_bool(v) is False, f"parse_bool({v!r}) != False"


def test_none_and_default():
    assert parse_bool(None, default=True) is True
    assert parse_bool(None, default=False) is False
    assert parse_bool(None) is False  # default


def test_garbage_fallback():
    assert parse_bool("garbage", default=False) is False
    assert parse_bool("unknown", default=True) is True


def test_string_number():
    assert parse_bool("2") is True
    assert parse_bool("-0.5") is False
    assert parse_bool("0.8") is True


def test_whitespace():
    assert parse_bool("  true  ") is True
    assert parse_bool(" FALSE ") is False


def test_empty_string():
    # empty string → float conversion fails → falls back to default
    assert parse_bool("") is False
    assert parse_bool("  ") is False
