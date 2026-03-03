"""
SkillStoreClient — 与 OpenAkita Platform Skill Store 交互的客户端

功能：
- search: 搜索平台上的 Skill
- get_detail: 获取 Skill 详情
- install: 通过 installUrl 下载并安装 Skill 到本地
- rate: 为 Skill 评分
- submit_repo: 提交 GitHub 仓库供索引
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class SkillStoreClient:
    """Skill Store HTTP 客户端"""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.hub_api_url).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": f"OpenAkita/{self._get_version()}"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _get_version() -> str:
        try:
            from .._bundled_version import __version__
            return __version__
        except Exception:
            return "dev"

    async def search(
        self,
        query: str = "",
        category: str = "",
        trust_level: str = "",
        sort: str = "installs",
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        client = await self._get_client()
        params: dict[str, Any] = {"page": str(page), "limit": str(limit), "sort": sort}
        if query:
            params["q"] = query
        if category:
            params["category"] = category
        if trust_level:
            params["trustLevel"] = trust_level

        resp = await client.get("/skills", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_detail(self, skill_id: str) -> dict[str, Any]:
        client = await self._get_client()
        resp = await client.get(f"/skills/{skill_id}")
        resp.raise_for_status()
        return resp.json()

    async def install_skill(self, install_url: str, target_dir: Path | None = None) -> Path:
        """安装 Skill 到本地

        install_url 格式: owner/repo@skill_name 或完整 git URL
        """
        if target_dir is None:
            target_dir = settings.skills_path

        target_dir.mkdir(parents=True, exist_ok=True)

        if "@" in install_url and "/" in install_url:
            repo_part, skill_name = install_url.rsplit("@", 1)
            if not repo_part.startswith("http"):
                repo_part = f"https://github.com/{repo_part}"
        else:
            repo_part = install_url
            skill_name = install_url.rsplit("/", 1)[-1]

        skill_dir = target_dir / skill_name
        if skill_dir.exists():
            logger.info(f"Skill {skill_name} already exists, updating...")
            shutil.rmtree(skill_dir)

        try:
            git_exe = shutil.which("git")
            if git_exe is None:
                raise FileNotFoundError("git not found in PATH")

            result = subprocess.run(
                [git_exe, "clone", "--depth=1", repo_part, str(skill_dir)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git clone failed: {result.stderr}")

            git_dir = skill_dir / ".git"
            if git_dir.exists():
                shutil.rmtree(git_dir)

            logger.info(f"Installed skill: {skill_name} -> {skill_dir}")
            return skill_dir

        except Exception as e:
            if skill_dir.exists():
                shutil.rmtree(skill_dir, ignore_errors=True)
            raise RuntimeError(f"Failed to install skill '{skill_name}': {e}") from e

    async def rate(self, skill_id: str, score: int, comment: str = "", token: str = "") -> dict[str, Any]:
        client = await self._get_client()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = await client.post(
            f"/skills/{skill_id}/rate",
            json={"score": score, "comment": comment},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def submit_repo(self, repo_url: str) -> dict[str, Any]:
        client = await self._get_client()
        resp = await client.post(
            "/skills/submit-repo",
            json={"repoUrl": repo_url},
        )
        resp.raise_for_status()
        return resp.json()
