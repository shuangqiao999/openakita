import pytest

import openakita.api.routes.config as config_routes
from openakita.api.routes.config import (
    _PermissionModeBody,
    _apply_permission_mode_defaults,
    _mode_from_security,
    _normalize_permission_mode,
    write_permission_mode,
)


def test_permission_mode_accepts_trust_alias():
    assert _normalize_permission_mode("trust") == "yolo"
    assert _normalize_permission_mode("yolo") == "yolo"


def test_yolo_mode_syncs_low_interrupt_defaults():
    sec: dict = {"zones": {"default_zone": "protected"}}

    _apply_permission_mode_defaults(sec, "trust")

    assert sec["confirmation"]["mode"] == "yolo"
    assert sec["zones"]["default_zone"] == "workspace"
    assert sec["sandbox"]["enabled"] is False
    assert sec["self_protection"]["enabled"] is False
    assert sec["command_patterns"]["enabled"] is False
    assert _mode_from_security(sec) == "yolo"


def test_smart_mode_syncs_protection_defaults():
    sec: dict = {}

    _apply_permission_mode_defaults(sec, "smart")

    assert sec["confirmation"]["mode"] == "smart"
    assert sec["zones"]["default_zone"] == "controlled"
    assert sec["sandbox"]["enabled"] is True
    assert sec["self_protection"]["enabled"] is True
    assert sec["command_patterns"]["enabled"] is True


def test_cautious_mode_syncs_strict_defaults():
    sec: dict = {"zones": {"default_zone": "workspace"}}

    _apply_permission_mode_defaults(sec, "cautious")

    assert sec["confirmation"]["mode"] == "cautious"
    assert sec["zones"]["default_zone"] == "protected"
    assert sec["sandbox"]["enabled"] is True
    assert sec["self_protection"]["enabled"] is True
    assert sec["command_patterns"]["enabled"] is True


@pytest.mark.asyncio
async def test_write_permission_mode_fails_when_yaml_unreadable(monkeypatch):
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: None)

    result = await write_permission_mode(_PermissionModeBody(mode="smart"))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_write_permission_mode_fails_when_yaml_write_fails(monkeypatch):
    data = {"security": {}}
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: data)
    monkeypatch.setattr(config_routes, "_write_policies_yaml", lambda _data: False)

    result = await write_permission_mode(_PermissionModeBody(mode="smart"))

    assert result["status"] == "error"

