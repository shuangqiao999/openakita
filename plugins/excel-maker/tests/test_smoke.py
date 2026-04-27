from __future__ import annotations

import json
from pathlib import Path


def test_manifest_is_excel_first() -> None:
    manifest = json.loads((Path(__file__).resolve().parents[1] / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "excel-maker"
    assert "brain.access" in manifest["permissions"]
    assert "excel_build_workbook" in manifest["provides"]["tools"]
    assert "ppt" not in " ".join(manifest["provides"]["tools"])


def test_plugin_registers_excel_tools() -> None:
    import sys
    import types

    api_module = types.ModuleType("openakita.plugins.api")

    class PluginBase:
        pass

    class PluginAPI:
        pass

    api_module.PluginBase = PluginBase
    api_module.PluginAPI = PluginAPI
    sys.modules["openakita.plugins.api"] = api_module

    from plugin import _tool_definitions

    names = {item["name"] for item in _tool_definitions()}

    assert {
        "excel_start_project",
        "excel_import_workbook",
        "excel_profile_workbook",
        "excel_generate_report_plan",
        "excel_build_workbook",
        "excel_audit_workbook",
    }.issubset(names)


def test_public_serializers_do_not_expose_server_paths(tmp_path) -> None:
    import sys
    import types

    api_module = types.ModuleType("openakita.plugins.api")

    class PluginBase:
        pass

    class PluginAPI:
        pass

    api_module.PluginBase = PluginBase
    api_module.PluginAPI = PluginAPI
    sys.modules["openakita.plugins.api"] = api_module

    from excel_models import ArtifactKind, ArtifactRecord, WorkbookRecord
    from plugin import Plugin

    plugin = Plugin()
    workbook = WorkbookRecord(
        id="wb_test",
        filename="sales.csv",
        original_path=str(tmp_path / "sales.csv"),
        imported_path=str(tmp_path / "workbooks" / "sales.csv"),
        profile_path=str(tmp_path / "profile.json"),
        created_at=1,
        updated_at=1,
    )
    artifact = ArtifactRecord(
        id="art_test",
        project_id="proj_test",
        kind=ArtifactKind.WORKBOOK,
        path=str(tmp_path / "report.xlsx"),
        created_at=1,
    )

    public_workbook = plugin._public_workbook(workbook)
    public_artifact = plugin._public_artifact(artifact)

    assert "original_path" not in public_workbook
    assert "imported_path" not in public_workbook
    assert "profile_path" not in public_workbook
    assert "path" not in public_artifact
    assert public_artifact["download_url"] == "/artifacts/art_test/download"


def test_ui_asset_exists() -> None:
    root = Path(__file__).resolve().parents[1]

    assert (root / "ui" / "dist" / "index.html").is_file()
    assert (root / "ui" / "dist" / "_assets" / "styles.css").is_file()


def test_ui_uses_plugin_bridge_and_no_absolute_upload_path() -> None:
    html = (Path(__file__).resolve().parents[1] / "ui" / "dist" / "index.html").read_text(encoding="utf-8")

    assert 'PLUGIN_ID_DEFAULT = "excel-maker"' in html
    assert "bridge:api-request" in html
    assert "/api/plugins/" in html
    assert "workbook_id: wb.id" in html
    assert "wb.original_path" not in html

