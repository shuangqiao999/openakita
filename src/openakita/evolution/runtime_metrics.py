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


class RuntimeMetricsCollector:
    def __init__(self, data_dir: str | Path = "data/evolution/metrics") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self._data_dir / "last_collect.json"

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
        last_ts = 0.0

        try:
            from openakita.config import settings
            if getattr(settings, "runtime_metrics_incremental", True):
                last_ts = self._load_last_ts()
        except Exception:
            pass

        self._collect_memory_stats(snapshot)
        self._collect_tool_stats(snapshot, last_ts)
        self._collect_user_feedback(snapshot, last_ts)
        self._save_last_ts(time.time())
        return snapshot

    def _collect_memory_stats(self, snapshot: RuntimeSnapshot) -> None:
        try:
            from openakita.config import settings

            db_path = settings.data_dir.parent / "data" / "memory" / "openakita.db"
            if not db_path.exists():
                return
            import sqlite3

            conn = sqlite3.connect(str(db_path))
            cur = conn.execute("SELECT COUNT(*) FROM memories WHERE active_only IS NULL OR 1=1")
            snapshot.memory_total = cur.fetchone()[0]
            conn.close()
        except Exception:
            pass

    def _collect_tool_stats(self, snapshot: RuntimeSnapshot, last_ts: float = 0.0) -> None:
        try:
            from openakita.config import settings
            from openakita.evolution.pattern_learner import PatternLearner

            traces_dir = settings.data_dir / "react_traces"
            if not traces_dir.is_dir():
                return

            tool_total: dict[str, int] = defaultdict(int)
            tool_fails: dict[str, int] = defaultdict(int)

            trace_files = []
            for d in traces_dir.iterdir():
                if d.is_dir():
                    trace_files.extend(d.glob("*.json"))
            trace_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

            for f in trace_files[:50]:
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

    def _collect_user_feedback(self, snapshot: RuntimeSnapshot, last_ts: float = 0.0) -> None:
        try:
            from openakita.config import settings

            traces_dir = settings.data_dir / "react_traces"
            if not traces_dir.is_dir():
                return

            queries: list[str] = []
            trace_files = []
            for d in traces_dir.iterdir():
                if d.is_dir():
                    trace_files.extend(d.glob("*.json"))
            trace_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

            for f in trace_files[:30]:
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
