"""
运行时指标采集器

采集:
1. 工具调用频率 + 失败率
2. 检索命中率(记忆标记法)
3. 用户反馈(隐式)
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MEMORY_ID_RE = re.compile(r"<!--\s*memory_id:\s*(\S+)\s*-->")
_FAILURE_KW = re.compile(r"error|失败|timeout|超时|exception|异常|denied|refused", re.I)


@dataclass
class RuntimeSnapshot:
    timestamp: str = ""
    memory_total: int = 0
    retrieval_hit_rate: float = 0.0
    tool_frequencies: dict[str, int] = field(default_factory=dict)
    tool_failure_rates: dict[str, float] = field(default_factory=dict)
    user_corrections: int = 0
    repeated_queries: int = 0
    conversation_success_rate: float = 0.0
    conversation_avg_tokens: float = 0.0
    memory_usage_rate: float = 0.0


class RuntimeMetricsCollector:
    def __init__(self, data_dir: str | Path = "data/evolution/metrics") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self._data_dir / "last_collect.json"
        self._db_conn: Any = None

    def _get_db(self):
        if self._db_conn is not None:
            return self._db_conn
        from openakita.config import settings

        db_path = settings.data_dir / "memory" / "openakita.db"
        if not db_path.exists():
            return None
        import sqlite3

        self._db_conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db_conn.execute("PRAGMA journal_mode=WAL")
        return self._db_conn

    def close(self) -> None:
        if self._db_conn is not None:
            self._db_conn.close()
            self._db_conn = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _load_last_ts(self) -> float:
        if not self._state_file.exists():
            return 0.0
        try:
            return json.loads(self._state_file.read_text(encoding="utf-8")).get("ts", 0.0)
        except Exception:
            return 0.0

    def _save_last_ts(self, ts: float) -> None:
        self._state_file.write_text(
            json.dumps({"ts": ts, "time": datetime.now().isoformat()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def collect(self) -> RuntimeSnapshot:
        snapshot = RuntimeSnapshot(timestamp=datetime.now().isoformat())
        collect_start = time.time()
        last_ts = 0.0

        try:
            from openakita.config import settings
            if getattr(settings, "runtime_metrics_incremental", True):
                last_ts = self._load_last_ts()
        except Exception:
            pass

        self._collect_memory_stats(snapshot)
        trace_files = self._scan_traces_dir()
        self._collect_tool_stats(snapshot, last_ts, trace_files)
        self._collect_user_feedback(snapshot, last_ts, trace_files)
        self._collect_conversation_metrics(snapshot, last_ts, trace_files)
        self._collect_memory_usage_rate(snapshot)
        self._save_last_ts(collect_start)
        return snapshot

    @staticmethod
    def _scan_traces_dir() -> list:
        try:
            from openakita.config import settings

            traces_dir = settings.data_dir / "react_traces"
            if not traces_dir.is_dir():
                return []
            files = []
            for d in traces_dir.iterdir():
                if d.is_dir():
                    files.extend(d.glob("*.json"))
            files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            return files
        except Exception:
            return []

    def _collect_memory_stats(self, snapshot: RuntimeSnapshot) -> None:
        try:
            conn = self._get_db()
            if conn is None:
                return
            cur = conn.execute("SELECT COUNT(*) FROM memories")
            snapshot.memory_total = cur.fetchone()[0]
        except Exception:
            pass

    def _collect_tool_stats(self, snapshot: RuntimeSnapshot, last_ts: float = 0.0, trace_files: list | None = None) -> None:
        try:
            from openakita.evolution.pattern_learner import PatternLearner

            files = trace_files if trace_files is not None else self._scan_traces_dir()
            if not files:
                return

            tool_total: dict[str, int] = defaultdict(int)
            tool_fails: dict[str, int] = defaultdict(int)

            for f in files[:50]:
                try:
                    if last_ts > 0 and f.stat().st_mtime <= last_ts:
                        continue
                    data = json.loads(f.read_text(encoding="utf-8"))
                    raw = data.get("iterations", data.get("steps", []))
                    tools = PatternLearner._extract_tool_names(raw)
                    for tool in tools:
                        tool_total[tool] += 1
                    for step in raw:
                        result = str(step.get("result", step.get("output", "")))
                        if _FAILURE_KW.search(result):
                            for tool in PatternLearner._extract_tool_names([step]):
                                tool_fails[tool] += 1
                except Exception:
                    continue

            snapshot.tool_frequencies = dict(sorted(tool_total.items(), key=lambda x: -x[1])[:20])
            for tool, total in tool_total.items():
                if total > 0:
                    snapshot.tool_failure_rates[tool] = round(tool_fails.get(tool, 0) / total, 3)
        except Exception:
            pass

    def _collect_user_feedback(self, snapshot: RuntimeSnapshot, last_ts: float = 0.0, trace_files: list | None = None) -> None:
        try:
            files = trace_files if trace_files is not None else self._scan_traces_dir()
            if not files:
                return

            queries: list[str] = []

            for f in files[:30]:
                try:
                    if last_ts > 0 and f.stat().st_mtime <= last_ts:
                        continue
                    data = json.loads(f.read_text(encoding="utf-8"))
                    for step in data.get("iterations", data.get("steps", [])):
                        q = step.get("user_message", step.get("query", ""))
                        if q:
                            queries.append(q)
                    if data.get("user_corrected"):
                        snapshot.user_corrections += 1
                except Exception:
                    continue

            last_q = ""
            for q in queries:
                if q == last_q:
                    snapshot.repeated_queries += 1
                last_q = q
        except Exception:
            pass

    def save_snapshot(self, snapshot: RuntimeSnapshot) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._data_dir / f"{ts}_snapshot.json"
        path.write_text(
            json.dumps(asdict(snapshot), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    @staticmethod
    def compute_hit_rate(injected_ids: set[str], referenced_ids: set[str]) -> float:
        if not injected_ids:
            return 1.0
        return len(injected_ids & referenced_ids) / len(injected_ids)

    @staticmethod
    def extract_memory_ids(text: str) -> set[str]:
        return set(_MEMORY_ID_RE.findall(text))

    @staticmethod
    def _extract_total_tokens(data: dict) -> int:
        if "total_tokens" in data:
            tt = data["total_tokens"]
            if isinstance(tt, dict):
                return tt.get("input", 0) + tt.get("output", 0)
            if isinstance(tt, (int, float)):
                return int(tt)
        total = 0
        for step in data.get("iterations", []):
            total += step.get("tokens_used", 0)
            tokens = step.get("tokens", {})
            if isinstance(tokens, dict):
                total += tokens.get("input", 0) + tokens.get("output", 0)
        return total

    def _collect_conversation_metrics(self, snapshot: RuntimeSnapshot, last_ts: float = 0.0, trace_files: list | None = None) -> None:
        try:
            files = trace_files if trace_files is not None else self._scan_traces_dir()
            if not files:
                return

            total = 0
            succeeded = 0
            all_tokens = []
            for f in files[:50]:
                try:
                    if last_ts > 0 and f.stat().st_mtime <= last_ts:
                        continue
                    data = json.loads(f.read_text(encoding="utf-8"))
                    total += 1
                    if data.get("result") in ("success", "completed"):
                        succeeded += 1
                    all_tokens.append(self._extract_total_tokens(data))
                except Exception:
                    continue

            if total > 0:
                snapshot.conversation_success_rate = round(succeeded / total, 3)
                snapshot.conversation_avg_tokens = round(
                    sum(all_tokens) / max(len(all_tokens), 1), 1
                )
        except Exception:
            pass

    def _collect_memory_usage_rate(self, snapshot: RuntimeSnapshot) -> None:
        try:
            conn = self._get_db()
            if conn is None:
                return
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            used = 0
            try:
                used = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE access_count > 0"
                ).fetchone()[0]
            except Exception:
                used = 0
            if used == 0 and total > 0:
                try:
                    used = conn.execute(
                        "SELECT COUNT(*) FROM memories WHERE last_accessed_at IS NOT NULL"
                    ).fetchone()[0]
                except Exception:
                    pass
                if used == 0:
                    try:
                        used = conn.execute(
                            "SELECT COUNT(*) FROM memories WHERE created_at > datetime('now', '-7 days')"
                        ).fetchone()[0]
                    except Exception:
                        pass
            if total > 0:
                snapshot.memory_usage_rate = round(used / total, 3)
        except Exception:
            pass

    def get_last_tuning_time(self) -> float:
        path = self._data_dir / "last_memory_tuning.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8")).get("ts", 0.0)
            except Exception:
                return 0.0
        return 0.0

    def record_tuning_time(self) -> None:
        (self._data_dir / "last_memory_tuning.json").write_text(
            json.dumps({"ts": time.time()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
