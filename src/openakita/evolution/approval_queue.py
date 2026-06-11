"""
审批队列

存储和管理待人工审批的高风险变更请求。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass
class ApprovalRequest:
    id: str = ""
    source: str = ""
    agent_role: str = ""
    risk_level: str = "medium"
    title: str = ""
    description: str = ""
    target_file: str = ""
    original_content: str = ""
    proposed_content: str = ""
    hypothesis: str = ""
    metrics_before: dict = field(default_factory=dict)
    metrics_after: dict = field(default_factory=dict)
    status: str = "pending"
    created_at: str = ""
    resolved_at: str = ""
    resolved_by: str = ""
    reject_reason: str = ""
    apply_error: str = ""


class ApprovalQueue:
    def __init__(self, data_dir: str | Path = "data/evolution/approvals") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, req_id: str) -> Path | None:
        if not _SAFE_ID_RE.match(req_id):
            return None
        path = (self._data_dir / f"{req_id}.json").resolve()
        if not path.is_relative_to(self._data_dir.resolve()):
            return None
        return path

    def submit(self, req: ApprovalRequest) -> str:
        if not req.id:
            req.id = uuid.uuid4().hex[:12]
        if not req.created_at:
            req.created_at = datetime.now().isoformat()
        req.status = "pending"
        self._save(req)
        logger.info("[ApprovalQueue] 新审批请求: %s — %s", req.id, req.title)
        return req.id

    def list_all(self, status: str | None = None) -> list[dict]:
        results = []
        for f in sorted(
            self._data_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if status and data.get("status") != status:
                    continue
                results.append(data)
            except Exception:
                continue
        return results

    def get(self, req_id: str) -> dict | None:
        path = self._safe_path(req_id)
        if not path or not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def approve_and_apply(self, req_id: str) -> tuple[bool, str]:
        data = self.get(req_id)
        if not data:
            return False, "请求不存在"
        if data.get("status") not in ("pending", "approved"):
            return False, f"当前状态 {data.get('status')} 不可操作"

        target_file = data.get("target_file", "")
        original = data.get("original_content", "")
        proposed = data.get("proposed_content", "")

        if not target_file or not original or not proposed:
            data["status"] = "approved"
            data["resolved_at"] = datetime.now().isoformat()
            data["resolved_by"] = "human"
            data["apply_error"] = "变更内容不完整（仅标记批准，需手动应用）"
            self._save_dict(req_id, data)
            return True, "已批准（无可自动应用的内容，需手动处理）"

        from ..config import settings

        target = (settings.project_root / target_file).resolve()
        if not target.is_relative_to(settings.project_root.resolve()):
            return False, "路径遍历检测"
        if not target.exists():
            return False, "目标文件不存在"

        try:
            content = target.read_text(encoding="utf-8")
        except OSError as e:
            return False, f"无法读取目标文件: {e}"

        from .experiment_loop import ExperimentLoop

        new_content, match_err = ExperimentLoop._fuzzy_match_and_replace(content, original, proposed)
        if new_content is None:
            data["status"] = "pending"
            data["apply_error"] = f"无法匹配: {match_err}"
            self._save_dict(req_id, data)
            return False, "无法匹配原始片段，已恢复为待审批状态"

        try:
            target.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return False, f"写入目标文件失败: {e}"

        data["status"] = "applied"
        data["resolved_at"] = datetime.now().isoformat()
        data["resolved_by"] = "human"
        data["apply_error"] = ""
        self._save_dict(req_id, data)
        logger.info("[ApprovalQueue] 已批准并应用: %s → %s", req_id, target_file)
        return True, "变更已批准并应用"

    def reject(self, req_id: str, reason: str = "") -> bool:
        data = self.get(req_id)
        if not data or data.get("status") not in ("pending", "approved"):
            return False
        data["status"] = "rejected"
        data["resolved_at"] = datetime.now().isoformat()
        data["resolved_by"] = "human"
        data["reject_reason"] = reason
        self._save_dict(req_id, data)
        logger.info("[ApprovalQueue] 已拒绝: %s", req_id)
        return True

    def pending_count(self) -> int:
        count = 0
        for f in self._data_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("status") == "pending":
                    count += 1
            except Exception:
                continue
        return count

    def _save(self, req: ApprovalRequest) -> None:
        path = self._safe_path(req.id)
        if path:
            path.write_text(json.dumps(asdict(req), ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_dict(self, req_id: str, data: dict) -> None:
        path = self._safe_path(req_id)
        if path:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
