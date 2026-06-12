"""
.env 参数动态调优器

功能:
1. 读取/修改 .env 文件
2. 原子写入(tmp → rename) + 备份
3. 回滚恢复 + 自动清理
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_BACKUP_AGE_DAYS = 7


class EnvTuner:
    def __init__(self, env_path: str | Path, backup_dir: str | Path = ".env.backups") -> None:
        self._env_path = Path(env_path)
        self._backup_dir = Path(backup_dir)
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    def read(self, key: str) -> str | None:
        if not self._env_path.exists():
            return None
        for line in self._env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
                val = stripped.split("=", 1)[1].strip()
                return val.strip('"').strip("'")
        return None

    def apply(self, key: str, value: str) -> Path | None:
        """原子修改 .env，返回备份文件路径"""
        content = ""
        if self._env_path.exists():
            content = self._env_path.read_text(encoding="utf-8")

        backup = self._backup_dir / f"env_backup_{int(time.time() * 1000)}"
        if self._env_path.exists():
            shutil.copy2(self._env_path, backup)

        new_lines = []
        found = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
                new_lines.append(f"{key}={value}")
                found = True
            else:
                new_lines.append(line)

        if not found:
            if new_lines and new_lines[-1] != "":
                new_lines.append("")
            new_lines.append(f"{key}={value}")

        new_content = "\n".join(new_lines).strip() + "\n"

        tmp = self._env_path.with_suffix(".tmp")
        tmp.write_text(new_content, encoding="utf-8")
        tmp.replace(self._env_path)

        logger.info("[EnvTuner] %s=%s (备份: %s)", key, value, backup.name)
        return backup if backup.exists() else None

    def rollback(self, backup_path: Path) -> None:
        if backup_path.exists():
            shutil.copy2(backup_path, self._env_path)
            backup_path.unlink()
            logger.info("[EnvTuner] 回滚: %s", backup_path.name)

    def cleanup_backups(self, max_age_days: int = _MAX_BACKUP_AGE_DAYS) -> None:
        cutoff = time.time() - max_age_days * 86400
        for f in self._backup_dir.glob("env_backup_*"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    logger.debug("[EnvTuner] 清理过期备份: %s", f.name)
            except Exception:
                pass
