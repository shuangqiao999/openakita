"""
Agent package import/export routes + Hub/Store proxy routes.

Local routes for the Setup Center frontend to call:
- POST /api/agents/package/export     — export agent to .akita-agent
- POST /api/agents/package/import     — import from .akita-agent
- POST /api/agents/package/inspect    — preview package contents
- GET  /api/agents/package/exportable — list exportable agents
- GET  /api/hub/agents                — proxy search Agent Store
- GET  /api/hub/agents/{id}           — proxy get Agent detail
- POST /api/hub/agents/{id}/install   — download + install from Hub
- GET  /api/hub/skills                — proxy search Skill Store
- GET  /api/hub/skills/{id}           — proxy get Skill detail
- POST /api/hub/skills/{id}/install   — install Skill from Store
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


def _project_root() -> Path:
    try:
        from openakita.config import settings
        return Path(settings.project_root)
    except Exception:
        return Path.cwd()


def _get_stores():
    from openakita.config import settings
    root = Path(settings.project_root)

    from openakita.agents.profile import ProfileStore
    agents_dir = root / "data" / "agents"
    profile_store = ProfileStore(agents_dir)

    skills_dir = Path(settings.skills_path)
    return profile_store, skills_dir, root


def _reload_skills(request) -> None:
    """Trigger skill reload on the running agent after installing from platform.

    Uses the same mechanism as POST /api/skills/reload — access the live
    agent's skill_loader to re-scan all skill directories.
    Best-effort: failures are logged but never break the install flow.
    """
    try:
        from openakita.core.agent import Agent

        agent = getattr(request.app.state, "agent", None)
        actual_agent = agent
        if not isinstance(agent, Agent):
            actual_agent = getattr(agent, "_local_agent", None)
        if actual_agent is None:
            logger.debug("Skill reload skipped: agent not initialized")
            return

        loader = getattr(actual_agent, "skill_loader", None)
        if not loader:
            logger.debug("Skill reload skipped: no skill_loader on agent")
            return

        count = loader.load_all(Path(_project_root()))
        logger.info(f"Skills reloaded after platform install: {count} loaded")
    except Exception as e:
        logger.warning(f"Skill reload after platform install failed (non-blocking): {e}")


class ExportRequest(BaseModel):
    profile_id: str
    author_name: str = ""
    author_url: str = ""
    version: str = "1.0.0"
    include_skills: list[str] | None = None


@router.post("/api/agents/package/export")
async def export_agent(req: ExportRequest):
    """Export an agent profile as a .akita-agent package."""
    from openakita.agents.packager import AgentPackager, PackageError

    profile_store, skills_dir, root = _get_stores()
    output_dir = root / "data" / "agent_packages"

    packager = AgentPackager(
        profile_store=profile_store,
        skills_dir=skills_dir,
        output_dir=output_dir,
    )

    try:
        output_path = packager.package(
            profile_id=req.profile_id,
            author_name=req.author_name,
            author_url=req.author_url,
            version=req.version,
            include_skills=req.include_skills,
        )
    except PackageError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return FileResponse(
        path=str(output_path),
        media_type="application/x-akita-agent",
        filename=output_path.name,
    )


@router.post("/api/agents/package/import")
async def import_agent(
    request: Request,
    file: UploadFile = File(...),
    force: bool = False,
):
    """Import an agent from an uploaded .akita-agent package."""
    from openakita.agents.packager import AgentInstaller, PackageError

    profile_store, skills_dir, _ = _get_stores()

    with tempfile.NamedTemporaryFile(
        suffix=".akita-agent", delete=False
    ) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        installer = AgentInstaller(
            profile_store=profile_store,
            skills_dir=skills_dir,
        )
        profile = installer.install(tmp_path, force=force)
    except PackageError as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    _reload_skills(request)

    return {
        "message": "Agent imported successfully",
        "profile": profile.to_dict(),
    }


@router.post("/api/agents/package/inspect")
async def inspect_package(file: UploadFile = File(...)):
    """Preview the contents of an uploaded .akita-agent package."""
    from openakita.agents.packager import AgentInstaller, PackageError

    profile_store, skills_dir, _ = _get_stores()

    with tempfile.NamedTemporaryFile(
        suffix=".akita-agent", delete=False
    ) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        installer = AgentInstaller(
            profile_store=profile_store,
            skills_dir=skills_dir,
        )
        info = installer.inspect(tmp_path)
    except PackageError as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    return info


@router.get("/api/agents/package/exportable")
async def list_exportable():
    """List all agent profiles that can be exported."""
    profile_store, _, _ = _get_stores()
    profiles = profile_store.list_all(include_hidden=False)

    return {
        "agents": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "type": p.type.value,
                "icon": p.icon,
                "color": p.color,
                "category": p.category,
                "skills_count": len(p.skills) if p.skills else 0,
                "hub_source": p.hub_source,
            }
            for p in profiles
        ]
    }


# ---------------------------------------------------------------------------
# Hub proxy routes — forward requests to the OpenAkita Platform
# ---------------------------------------------------------------------------

def _get_hub_client():
    from openakita.hub import AgentHubClient
    return AgentHubClient()


def _get_skill_client():
    from openakita.hub import SkillStoreClient
    return SkillStoreClient()


@router.get("/api/hub/agents")
async def hub_search_agents(
    q: str = "",
    category: str = "",
    sort: str = "downloads",
    page: int = 1,
    limit: int = 20,
):
    """Proxy search to platform Agent Store."""
    client = _get_hub_client()
    try:
        result = await client.search(query=q, category=category, sort=sort, page=page, limit=limit)
        return result
    except Exception as e:
        logger.warning(f"Hub search agents unavailable (remote platform may be offline): {e}")
        raise HTTPException(
            status_code=502,
            detail="远程 Agent Store 暂不可用。本地 Agent 导入导出功能不受影响。",
        )
    finally:
        await client.close()


@router.get("/api/hub/agents/{agent_id}")
async def hub_agent_detail(agent_id: str):
    """Proxy Agent detail from platform."""
    client = _get_hub_client()
    try:
        return await client.get_detail(agent_id)
    except Exception as e:
        logger.warning(f"Hub agent detail unavailable: {e}")
        raise HTTPException(status_code=502, detail="远程 Agent Store 暂不可用")
    finally:
        await client.close()


@router.post("/api/hub/agents/{agent_id}/install")
async def hub_install_agent(request: Request, agent_id: str, force: bool = False):
    """Download agent from hub and install locally."""
    client = _get_hub_client()
    try:
        package_path = await client.download(agent_id)
    except Exception as e:
        logger.warning(f"Hub download unavailable: {e}")
        raise HTTPException(status_code=502, detail="远程 Agent Store 暂不可用，无法下载。可通过 .akita-agent 文件本地导入。")
    finally:
        await client.close()

    from openakita.agents.packager import AgentInstaller, PackageError

    profile_store, skills_dir, _ = _get_stores()
    installer = AgentInstaller(profile_store=profile_store, skills_dir=skills_dir)

    try:
        profile = installer.install(package_path, force=force)
    except PackageError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if profile.hub_source is None:
        profile.hub_source = {}
    profile.hub_source.update({
        "platform": "openakita",
        "agent_id": agent_id,
        "installed_at": datetime.now().isoformat(),
    })
    profile_store.save(profile)

    _reload_skills(request)

    return {
        "message": "Agent installed from Hub",
        "profile": profile.to_dict(),
    }


@router.get("/api/hub/skills")
async def hub_search_skills(
    q: str = "",
    category: str = "",
    trust_level: str = "",
    sort: str = "installs",
    page: int = 1,
    limit: int = 20,
):
    """Proxy search to platform Skill Store."""
    client = _get_skill_client()
    try:
        result = await client.search(
            query=q, category=category, trust_level=trust_level,
            sort=sort, page=page, limit=limit,
        )
        return result
    except Exception as e:
        logger.warning(f"Hub search skills unavailable (remote platform may be offline): {e}")
        raise HTTPException(
            status_code=502,
            detail="远程 Skill Store 暂不可用。本地技能管理和 skills.sh 市场不受影响。",
        )
    finally:
        await client.close()


@router.get("/api/hub/skills/{skill_id}")
async def hub_skill_detail(skill_id: str):
    """Proxy Skill detail from platform."""
    client = _get_skill_client()
    try:
        return await client.get_detail(skill_id)
    except Exception as e:
        logger.warning(f"Hub skill detail unavailable: {e}")
        raise HTTPException(status_code=502, detail="远程 Skill Store 暂不可用")
    finally:
        await client.close()


@router.post("/api/hub/skills/{skill_id}/install")
async def hub_install_skill(request: Request, skill_id: str):
    """Get skill info from platform and install locally."""
    client = _get_skill_client()
    try:
        detail = await client.get_detail(skill_id)
    except Exception as e:
        logger.warning(f"Hub skill install - cannot reach platform: {e}")
        raise HTTPException(
            status_code=502,
            detail="远程 Skill Store 暂不可用。可在「技能管理 → 浏览市场」通过 skills.sh 安装，或使用 install_skill 从 GitHub 安装。",
        )

    skill = detail.get("skill", detail)
    install_url = skill.get("installUrl", "")
    if not install_url:
        await client.close()
        raise HTTPException(status_code=400, detail="该 Skill 没有安装地址")

    try:
        skill_dir = await client.install_skill(install_url, skill_id=skill_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"安装失败: {e}")
    finally:
        await client.close()

    _reload_skills(request)

    return {
        "message": "Skill installed from Store",
        "skill_name": skill.get("name", skill_id),
        "skill_dir": str(skill_dir),
        "trust_level": skill.get("trustLevel", "community"),
    }
