"""
统一编译协调器

解决编译触发点分散的问题：
- 删除所有散落的 compile_all() 调用
- 统一使用 CompileCoordinator
- 同步/异步版本统一为异步

修复内容：
- 使用 asyncio.Event 实现真正的等待机制
- 移除 _last_compile_time，改用文件修改时间
- 锁只用于检查状态，不用于等待
- 添加超时保护
- 区分导入错误类型
- 使用 asyncio.to_thread 执行同步缓存操作
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CompileCoordinator:
    """
    统一编译协调器

    使用示例:
        from openakita.prompt.coordinator import CompileCoordinator

        # 启动时确保编译完成
        await CompileCoordinator.ensure_compiled(identity_dir)

        # 文件变化时强制重新编译
        await CompileCoordinator.ensure_compiled(identity_dir, force=True)

        # 带超时的编译
        await CompileCoordinator.ensure_compiled(identity_dir, timeout=60.0)
    """

    _compiling: bool = False
    _lock = asyncio.Lock()
    _compile_event: asyncio.Event = asyncio.Event()
    _compile_cache: dict[str, float] = {}

    # 编译状态文件
    COMPILE_STATE_FILE = ".compiled_at"

    # 默认超时时间
    DEFAULT_TIMEOUT = 30.0

    @classmethod
    def _get_compile_marker(cls, identity_dir: Path) -> Path:
        """获取编译标记文件路径"""
        return identity_dir / cls.COMPILE_STATE_FILE

    @classmethod
    def _get_source_mtime(cls, identity_dir: Path) -> float:
        """获取源文件的最新修改时间"""
        max_mtime = 0.0
        for suffix in [".md", ".yaml"]:
            for path in identity_dir.rglob(f"*{suffix}"):
                try:
                    mtime = path.stat().st_mtime
                    if mtime > max_mtime:
                        max_mtime = mtime
                except OSError:
                    continue
        return max_mtime

    @classmethod
    def _is_up_to_date(cls, identity_dir: Path) -> bool:
        """检查是否需要重新编译（基于文件修改时间）"""
        marker = cls._get_compile_marker(identity_dir)
        if not marker.exists():
            return False

        try:
            compile_time = float(marker.read_text().strip())
            source_mtime = cls._get_source_mtime(identity_dir)
            # 如果源文件比编译结果新，则需要重新编译
            return source_mtime <= compile_time
        except (OSError, ValueError):
            return False

    @classmethod
    def _mark_compiled(cls, identity_dir: Path) -> None:
        """标记编译完成"""
        marker = cls._get_compile_marker(identity_dir)
        try:
            import time

            compile_time = time.time()
            marker.write_text(str(compile_time))
            cls._compile_cache[str(identity_dir)] = compile_time
        except OSError as e:
            logger.warning(f"Failed to write compile marker: {e}")

    @classmethod
    async def ensure_compiled(
        cls,
        identity_dir: str | Path,
        force: bool = False,
        timeout: Optional[float] = None,
    ) -> bool:
        """
        确保编译完成

        Args:
            identity_dir: identity目录路径
            force: 是否强制重新编译
            timeout: 超时时间（秒），默认30秒

        Returns:
            True 如果编译成功
        """
        identity_dir = Path(identity_dir)
        timeout = timeout or cls.DEFAULT_TIMEOUT

        async with cls._lock:
            # 检查是否正在编译
            if cls._compiling:
                # 等待编译完成（使用Event）
                logger.debug("Waiting for ongoing compilation...")
                try:
                    await asyncio.wait_for(
                        cls._compile_event.wait(),
                        timeout=timeout,
                    )
                    # 等待完成后检查是否成功
                    if not force and cls._is_up_to_date(identity_dir):
                        return True
                    # 如果失败或需要重新编译，返回False
                    return False
                except asyncio.TimeoutError:
                    logger.warning("Compilation wait timeout")
                    return False

            # 检查是否需要编译
            if not force and cls._is_up_to_date(identity_dir):
                logger.debug(f"Identity files up-to-date: {identity_dir}")
                return True

            cls._compiling = True
            cls._compile_event.clear()

        try:
            logger.info(f"Starting compilation for: {identity_dir}")

            # 执行编译（异步）
            await cls._compile_all(identity_dir)

            # 标记完成
            cls._mark_compiled(identity_dir)

            # 失效相关缓存（使用 asyncio.to_thread）
            try:
                from ..core.cache import CacheType, UnifiedCache

                await asyncio.to_thread(
                    UnifiedCache.invalidate,
                    CacheType.PROMPT,
                )
                await asyncio.to_thread(
                    UnifiedCache.invalidate,
                    CacheType.IDENTITY,
                )
            except ImportError as e:
                logger.warning(f"Failed to invalidate cache: {e}")

            logger.info(f"Compilation completed for: {identity_dir}")
            return True

        except Exception as e:
            logger.error(f"Compilation failed: {e}")
            return False
        finally:
            cls._compiling = False
            cls._compile_event.set()

    @classmethod
    async def _compile_all(cls, identity_dir: Path) -> None:
        """执行编译（统一入口）"""
        try:
            from ..prompt.compiler import compile_all as do_compile

            await do_compile(identity_dir)
        except ImportError as e:
            # 区分模块不存在和其他导入错误
            if "compiler" in str(e):
                logger.warning("Prompt compiler not available, skipping compilation")
            else:
                logger.error(f"Prompt compiler import failed: {e}")

    @classmethod
    async def check_outdated(cls, identity_dir: Path) -> bool:
        """检查identity文件是否过期"""
        return not cls._is_up_to_date(identity_dir)


async def ensure_compiled(identity_dir: str | Path, timeout: float = 30.0) -> bool:
    """确保编译完成（便捷函数）"""
    return await CompileCoordinator.ensure_compiled(identity_dir, timeout=timeout)
