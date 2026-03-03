"""
Agent Package handler — export_agent, import_agent, list_exportable_agents, inspect_agent_package.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class AgentPackageHandler:
    """Handles agent package import/export tool calls."""

    TOOLS = [
        "export_agent",
        "import_agent",
        "list_exportable_agents",
        "inspect_agent_package",
    ]

    def __init__(self, agent: Agent):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        try:
            if tool_name == "export_agent":
                return await self._export(params)
            elif tool_name == "import_agent":
                return await self._import(params)
            elif tool_name == "list_exportable_agents":
                return await self._list_exportable(params)
            elif tool_name == "inspect_agent_package":
                return await self._inspect(params)
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logger.error(f"AgentPackageHandler error ({tool_name}): {e}", exc_info=True)
            return f"❌ 操作失败: {e}"

    async def _export(self, params: dict[str, Any]) -> str:
        from ...agents.packager import AgentPackager

        profile_id = params.get("profile_id", "")
        if not profile_id:
            return "❌ 需要指定 profile_id"

        profile_store = self.agent.profile_store
        skills_dir = Path(self.agent.base_dir) / "skills"
        output_dir = Path(self.agent.base_dir) / "data" / "agent_packages"

        packager = AgentPackager(
            profile_store=profile_store,
            skills_dir=skills_dir,
            output_dir=output_dir,
        )

        output_path = packager.package(
            profile_id=profile_id,
            author_name=params.get("author_name", ""),
            author_url=params.get("author_url", ""),
            version=params.get("version", "1.0.0"),
            include_skills=params.get("include_skills"),
        )

        size_kb = output_path.stat().st_size / 1024
        return (
            f"✅ Agent 已导出！\n\n"
            f"📦 文件: {output_path}\n"
            f"📏 大小: {size_kb:.1f} KB\n\n"
            f"你可以将这个 `.akita-agent` 文件分享给其他用户导入使用。"
        )

    async def _import(self, params: dict[str, Any]) -> str:
        from ...agents.packager import AgentInstaller

        package_path = params.get("package_path", "")
        if not package_path:
            return "❌ 需要指定 package_path"

        path = Path(package_path)
        if not path.is_absolute():
            path = Path(self.agent.base_dir) / path

        profile_store = self.agent.profile_store
        skills_dir = Path(self.agent.base_dir) / "skills"

        installer = AgentInstaller(
            profile_store=profile_store,
            skills_dir=skills_dir,
        )

        force = params.get("force", False)
        profile = installer.install(path, force=force)

        return (
            f"✅ Agent 导入成功！\n\n"
            f"🤖 名称: {profile.name}\n"
            f"🆔 ID: {profile.id}\n"
            f"📝 描述: {profile.description}\n"
            f"🔧 技能: {', '.join(profile.skills) if profile.skills else '无'}\n\n"
            f"你现在可以在 Agent 列表中找到并使用这个 Agent。"
        )

    async def _list_exportable(self, params: dict[str, Any]) -> str:
        profile_store = self.agent.profile_store
        profiles = profile_store.list_all(include_hidden=False)

        if not profiles:
            return "当前没有可导出的 Agent。"

        lines = ["📋 可导出的 Agent 列表：\n"]
        for p in profiles:
            skills_count = len(p.skills) if p.skills else 0
            type_label = "系统" if p.is_system else "自定义"
            cat = f" [{p.category}]" if p.category else ""
            lines.append(
                f"- **{p.name}** (`{p.id}`) — {type_label}{cat}, "
                f"{skills_count} 个技能"
            )

        lines.append(f"\n共 {len(profiles)} 个 Agent 可导出。")
        lines.append("使用 `export_agent` 工具导出指定 Agent。")
        return "\n".join(lines)

    async def _inspect(self, params: dict[str, Any]) -> str:
        from ...agents.packager import AgentInstaller

        package_path = params.get("package_path", "")
        if not package_path:
            return "❌ 需要指定 package_path"

        path = Path(package_path)
        if not path.is_absolute():
            path = Path(self.agent.base_dir) / path

        profile_store = self.agent.profile_store
        skills_dir = Path(self.agent.base_dir) / "skills"

        installer = AgentInstaller(
            profile_store=profile_store,
            skills_dir=skills_dir,
        )

        info = installer.inspect(path)

        manifest = info["manifest"]
        profile = info["profile"]
        errors = info["validation_errors"]
        conflict = info["id_conflict"]

        lines = [
            f"📦 Agent 包预览\n",
            f"**名称**: {manifest.get('name', '?')}",
            f"**ID**: {manifest.get('id', '?')}",
            f"**版本**: {manifest.get('version', '?')}",
            f"**作者**: {manifest.get('author', {}).get('name', '?')}",
            f"**分类**: {manifest.get('category', '无')}",
            f"**大小**: {info['package_size'] / 1024:.1f} KB",
        ]

        if info["bundled_skills"]:
            lines.append(f"**捆绑技能**: {', '.join(info['bundled_skills'])}")
        if manifest.get("required_builtin_skills"):
            lines.append(
                f"**需要内置技能**: {', '.join(manifest['required_builtin_skills'])}"
            )

        if errors:
            lines.append(f"\n⚠️ 校验问题: {'; '.join(errors)}")
        if conflict:
            lines.append(f"\n⚠️ ID 冲突: 本地已存在 `{manifest.get('id')}`，导入时将自动重命名")

        if profile.get("custom_prompt"):
            prompt_preview = profile["custom_prompt"][:200]
            if len(profile["custom_prompt"]) > 200:
                prompt_preview += "..."
            lines.append(f"\n**提示词预览**: {prompt_preview}")

        lines.append("\n使用 `import_agent` 工具导入此 Agent。")
        return "\n".join(lines)


def create_handler(agent: Agent):
    """Factory function following the project convention."""
    handler = AgentPackageHandler(agent)
    return handler.handle
