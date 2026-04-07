"""
统一编译协调器

解决编译触发点分散的问题：
- 删除所有散落的 compile_all() 调用
- 统一使用 CompileCoordinator
- 同步/异步版本统一为异步
"""

import asyncio
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class CompileCoordinator:
    """
    统一编译协调器

    使用示例:
        from openakita.prompt.compiler import CompileCoordinator

        # 启动时确保编译完成
        await CompileCoordinator.ensure_compiled(identity_dir)

        # 文件变化时强制重新编译
        await CompileCoordinator.ensure_compiled(identity_dir, force=True)
    """

    _compiling: bool = False
    _last_compile_time: float = 0
    _lock = asyncio.Lock()
    _compile_cache: dict[str, float] = {}  # identity_dir -> last compile timestamp

    # 编译状态文件
    COMPILE_STATE_FILE = ".compiled_at"

    @classmethod
    def _get_compile_marker(cls, identity_dir: Path) -> Path:
        """获取编译标记文件路径"""
        return identity_dir / cls.COMPILE_STATE_FILE

    @classmethod
    def _is_up_to_date(cls, identity_dir: Path) -> bool:
        """检查是否需要重新编译"""
        marker = cls._get_compile_marker(identity_dir)
        if not marker.exists():
            return False

        try:
            compile_time = float(marker.read_text().strip())
            return compile_time >= cls._last_compile_time
        except (OSError, ValueError):
            return False

    @classmethod
    def _mark_compiled(cls, identity_dir: Path) -> None:
        """标记编译完成"""
        marker = cls._get_compile_marker(identity_dir)
        try:
            cls._last_compile_time = time.time()
            marker.write_text(str(cls._last_compile_time))
            cls._compile_cache[str(identity_dir)] = cls._last_compile_time
        except OSError as e:
            logger.warning(f"Failed to write compile marker: {e}")

    @classmethod
    async def ensure_compiled(cls, identity_dir: str | Path, force: bool = False) -> bool:
        """
        确保编译完成

        Args:
            identity_dir: identity目录路径
            force: 是否强制重新编译

        Returns:
            True 如果编译成功
        """
        identity_dir = Path(identity_dir)

        async with cls._lock:
            # 检查是否正在编译
            if cls._compiling:
                # 等待当前编译完成
                logger.debug("Waiting for ongoing compilation...")
                # 这里需要等待，可以用一个事件来通知
                await asyncio.sleep(0.1)
                return await cls.ensure_compiled(identity_dir, force)

            # 检查是否需要编译
            if not force and cls._is_up_to_date(identity_dir):
                logger.debug(f"Identity files up-to-date: {identity_dir}")
                return True

            cls._compiling = True
            try:
                logger.info(f"Starting compilation for: {identity_dir}")

                # 执行编译
                await cls._compile_all(identity_dir)

                # 标记完成
                cls._mark_compiled(identity_dir)

                # 失效相关缓存
                from ..core.cache import CacheType, UnifiedCache

                UnifiedCache.invalidate(CacheType.PROMPT)
                UnifiedCache.invalidate(CacheType.IDENTITY)

                logger.info(f"Compilation completed for: {identity_dir}")
                return True

            except Exception as e:
                logger.error(f"Compilation failed: {e}")
                return False
            finally:
                cls._compiling = False

    @classmethod
    async def _compile_all(cls, identity_dir: Path) -> None:
        """执行编译（统一入口）"""
        try:
            # 导入编译模块
            from ..prompt.compiler import compile_all as do_compile

            await do_compile(identity_dir)
        except ImportError:
            # 如果编译模块不存在，使用备用逻辑
            logger.warning("Prompt compiler not available, skipping compilation")

    @classmethod
    async def check_outdated(cls, identity_dir: Path) -> bool:
        """检查identity文件是否过期"""
        return not cls._is_up_to_date(identity_dir)


# 便捷函数
async def ensure_compiled(identity_dir: str | Path) -> bool:
    """确保编译完成（便捷函数）"""
    return await CompileCoordinator.ensure_compiled(identity_dir)
