"""
AgentFactory — 根据 AgentProfile 创建差异化 Agent 实例
AgentInstancePool — per-session + per-profile 实例管理 + 空闲回收

Pool key 格式: ``{session_id}::{profile_id}``
同一会话可持有多个不同 profile 的 Agent 实例并行运行。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from .profile import AgentProfile, SkillsMode

if TYPE_CHECKING:
    from openakita.core.agent import Agent

logger = logging.getLogger(__name__)

_IDLE_TIMEOUT_SECONDS = 30 * 60  # 30 分钟空闲回收
_REAP_INTERVAL_SECONDS = 60  # 每分钟检查一次


class AgentFactory:
    """
    根据 AgentProfile 创建 Agent 实例。

    - 按 profile 配置过滤技能
    - 注入自定义提示词
    - 设置 agent name/icon
    """

    async def create(self, profile: AgentProfile, **kwargs: Any) -> Agent:
        from openakita.core.agent import Agent

        agent = Agent(name=profile.get_display_name(), **kwargs)
        agent._agent_profile = profile

        await agent.initialize(start_scheduler=False, lightweight=True)

        self._apply_skill_filter(agent, profile)

        if profile.custom_prompt:
            agent._custom_prompt_suffix = profile.custom_prompt

        logger.info(
            f"AgentFactory created: {profile.id} "
            f"(skills_mode={profile.skills_mode.value}, "
            f"skills={profile.skills})"
        )
        return agent

    @staticmethod
    def _apply_skill_filter(agent: Agent, profile: AgentProfile) -> None:
        if profile.skills_mode == SkillsMode.ALL or not profile.skills:
            return

        registry = agent.skill_registry
        all_skills = [skill.name for skill in registry.list_all()]

        if profile.skills_mode == SkillsMode.INCLUSIVE:
            keep = set(profile.skills)
            for skill_name in all_skills:
                if skill_name not in keep:
                    registry.unregister(skill_name)
        elif profile.skills_mode == SkillsMode.EXCLUSIVE:
            exclude = set(profile.skills)
            for skill_name in all_skills:
                if skill_name in exclude:
                    registry.unregister(skill_name)


class _PoolEntry:
    __slots__ = ("agent", "profile_id", "session_id", "created_at", "last_used")

    def __init__(self, agent: Agent, profile_id: str, session_id: str):
        self.agent = agent
        self.profile_id = profile_id
        self.session_id = session_id
        self.created_at = time.monotonic()
        self.last_used = time.monotonic()

    def touch(self) -> None:
        self.last_used = time.monotonic()

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used

    @property
    def pool_key(self) -> str:
        return f"{self.session_id}::{self.profile_id}"


class AgentInstancePool:
    """
    Agent 实例池 — per-session + per-profile 绑定 + 空闲自动回收。

    Pool key 格式: ``{session_id}::{profile_id}``

    同一会话可同时持有多个不同 profile 的 Agent 实例。
    例如 session_123 可以同时运行 default, browser-agent, data-analyst。
    """

    def __init__(
        self,
        factory: AgentFactory | None = None,
        idle_timeout: float = _IDLE_TIMEOUT_SECONDS,
    ):
        self._factory = factory or AgentFactory()
        self._idle_timeout = idle_timeout
        # Key: "{session_id}::{profile_id}"
        self._pool: dict[str, _PoolEntry] = {}
        # Per-composite-key locks for concurrent creation
        self._create_locks: dict[str, asyncio.Lock] = {}
        self._reaper_task: asyncio.Task | None = None

    @staticmethod
    def _make_key(session_id: str, profile_id: str) -> str:
        return f"{session_id}::{profile_id}"

    async def start(self) -> None:
        self._reaper_task = asyncio.create_task(self._reap_loop())
        logger.info("AgentInstancePool reaper started")

    async def stop(self) -> None:
        if self._reaper_task:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
        self._pool.clear()
        logger.info("AgentInstancePool stopped")

    async def get_or_create(
        self, session_id: str, profile: AgentProfile,
    ) -> Agent:
        """获取已有实例或创建新实例。

        Key = session_id::profile_id，同 session 不同 profile 各自独立。
        All dict operations are safe under asyncio's single-threaded event loop;
        only the async create_lock is needed to serialize factory.create() calls.
        """
        key = self._make_key(session_id, profile.id)

        entry = self._pool.get(key)
        if entry:
            entry.touch()
            return entry.agent

        if key not in self._create_locks:
            self._create_locks[key] = asyncio.Lock()
        create_lock = self._create_locks[key]

        async with create_lock:
            entry = self._pool.get(key)
            if entry:
                entry.touch()
                return entry.agent

            agent = await self._factory.create(profile)
            new_entry = _PoolEntry(agent, profile.id, session_id)
            self._pool[key] = new_entry

        logger.info(
            f"Pool created agent: session={session_id}, profile={profile.id}"
        )
        return agent

    def get_existing(
        self, session_id: str, profile_id: str | None = None,
    ) -> Agent | None:
        """Return an existing Agent without creating a new one.

        If *profile_id* is given, looks up the exact (session, profile) pair.
        Otherwise returns the first (and typically only) agent for the session
        — used by control endpoints (cancel/skip/insert).
        """
        if profile_id:
            key = self._make_key(session_id, profile_id)
            entry = self._pool.get(key)
            if entry:
                entry.touch()
                return entry.agent
            return None

        for entry in self._pool.values():
            if entry.session_id == session_id:
                entry.touch()
                return entry.agent
        return None

    def get_all_for_session(self, session_id: str) -> list[_PoolEntry]:
        """Return all pool entries for a given session."""
        return [e for e in self._pool.values() if e.session_id == session_id]

    def release(self, session_id: str, profile_id: str | None = None) -> None:
        """标记实例进入空闲等待回收。"""
        if profile_id:
            key = self._make_key(session_id, profile_id)
            entry = self._pool.get(key)
            if entry:
                entry.touch()
        else:
            for entry in self._pool.values():
                if entry.session_id == session_id:
                    entry.touch()

    def get_stats(self) -> dict:
        entries = list(self._pool.values())

        sessions: dict[str, list[dict]] = {}
        for e in entries:
            sessions.setdefault(e.session_id, []).append({
                "profile_id": e.profile_id,
                "idle_seconds": round(e.idle_seconds, 1),
            })

        return {
            "total": len(entries),
            "sessions": [
                {
                    "session_id": sid,
                    "profile_id": agents[0]["profile_id"],
                    "idle_seconds": min(a["idle_seconds"] for a in agents),
                    "agents": agents,
                }
                for sid, agents in sessions.items()
            ],
        }

    @staticmethod
    def _get_shared_profile_store():
        """Get the orchestrator's ProfileStore to share the _ephemeral dict."""
        try:
            from openakita.main import _orchestrator
            if _orchestrator and hasattr(_orchestrator, "_profile_store"):
                return _orchestrator._profile_store
        except (ImportError, AttributeError):
            pass
        return None

    async def _reap_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_REAP_INTERVAL_SECONDS)
                self._reap_idle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"AgentInstancePool reaper error: {e}")

    def _reap_idle(self) -> None:
        reaped_profile_ids: list[str] = []

        stale_locks = [k for k in self._create_locks if k not in self._pool]
        for k in stale_locks:
            lock = self._create_locks[k]
            if not lock.locked():
                self._create_locks.pop(k, None)

        to_remove = [
            key for key, entry in self._pool.items()
            if entry.idle_seconds > self._idle_timeout
        ]
        for key in to_remove:
            entry = self._pool.pop(key)
            reaped_profile_ids.append(entry.profile_id)
            logger.info(
                f"Pool reaped idle agent: session={entry.session_id}, "
                f"profile={entry.profile_id}, "
                f"idle={entry.idle_seconds:.0f}s"
            )
            try:
                if hasattr(entry.agent, 'shutdown'):
                    asyncio.ensure_future(entry.agent.shutdown())
            except Exception:
                pass

        # Clean up ephemeral profiles for reaped agents (outside lock)
        if reaped_profile_ids:
            try:
                store = self._get_shared_profile_store()
                if store:
                    for pid in reaped_profile_ids:
                        p = store.get(pid)
                        if p and getattr(p, "ephemeral", False):
                            store.remove_ephemeral(pid)
                            logger.info(f"Pool reaper cleaned ephemeral profile: {pid}")
            except Exception as e:
                logger.warning(f"Pool reaper ephemeral cleanup failed: {e}")
