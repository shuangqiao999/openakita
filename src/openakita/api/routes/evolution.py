"""
自进化系统监控 API
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/evolution", tags=["evolution"])


def _data_dir() -> Path:
    from openakita.config import settings

    return settings.data_dir / "evolution"


def _read_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _list_json_files(directory: Path, pattern: str = "*.json") -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)


# ── Dashboard ──


@router.get("/dashboard")
async def dashboard(request: Request):
    base = _data_dir()
    bench_dir = base / "benchmarks" / "results"
    exp_dir = base / "experiments"

    baseline = _read_json(base / "benchmarks" / "baseline.json")
    baseline_metrics = baseline.get("metrics", {}) if baseline else {}

    recent_benchmarks = []
    for f in _list_json_files(bench_dir)[:7]:
        data = _read_json(f)
        if data:
            recent_benchmarks.append(
                {"timestamp": data.get("timestamp", f.stem), "metrics": data.get("metrics", {})}
            )

    recent_experiments = []
    for f in _list_json_files(exp_dir)[:3]:
        data = _read_json(f)
        if isinstance(data, list):
            for entry in data[:3]:
                recent_experiments.append(
                    {
                        "timestamp": f.stem.split("_cycle")[0],
                        "description": entry.get("description", ""),
                        "action": entry.get("action", ""),
                        "delta": entry.get("delta", {}),
                    }
                )

    return {
        "health_score": round(baseline_metrics.get("efficiency_score", 0), 1),
        "baseline_metrics": baseline_metrics,
        "recent_benchmarks": recent_benchmarks,
        "recent_experiments": recent_experiments[:3],
    }


# ── Benchmarks ──


@router.get("/benchmarks")
async def list_benchmarks(request: Request, limit: int = 20):
    bench_dir = _data_dir() / "benchmarks" / "results"
    results = []
    for f in _list_json_files(bench_dir)[:limit]:
        data = _read_json(f)
        if data:
            data["_file"] = f.name
            results.append(data)
    return {"benchmarks": results, "total": len(results)}


# ── Experiments ──


@router.get("/experiments")
async def list_experiments(request: Request, limit: int = 50, status: str | None = None):
    exp_dir = _data_dir() / "experiments"
    all_entries = []
    for f in _list_json_files(exp_dir)[:limit]:
        data = _read_json(f)
        if isinstance(data, list):
            for entry in data:
                entry["_file"] = f.name
                entry["_timestamp"] = f.stem.split("_cycle")[0]
                if status and entry.get("action") != status:
                    continue
                all_entries.append(entry)
    return {"experiments": all_entries[:limit], "total": len(all_entries)}


@router.get("/experiments/{filename}")
async def get_experiment_detail(request: Request, filename: str):
    exp_dir = _data_dir() / "experiments"
    path = (exp_dir / filename).resolve()
    if not path.is_relative_to(exp_dir.resolve()) or not path.exists():
        raise HTTPException(404, "Experiment not found")
    data = _read_json(path)
    return {"filename": filename, "entries": data}


# ── Skills ──


@router.get("/skills")
async def list_auto_skills(request: Request):
    from openakita.config import settings

    skills_dir = settings.project_root / "skills"
    auto_skills = []
    if skills_dir.exists():
        for skill_dir in sorted(skills_dir.glob("*/metadata.json")):
            meta = _read_json(skill_dir) or {}
            if not meta.get("auto_generated"):
                continue
            parent = skill_dir.parent
            skill_md = parent / "SKILL.md"
            content = skill_md.read_text(encoding="utf-8")[:200] if skill_md.exists() else ""
            auto_skills.append(
                {
                    "name": parent.name,
                    "path": str(parent.relative_to(skills_dir)),
                    "description": content,
                    "created_at": meta.get("created_at", ""),
                    "enabled": meta.get("enabled", True),
                }
            )
    return {"skills": auto_skills, "total": len(auto_skills)}


class SkillUpdateRequest(BaseModel):
    enabled: bool


@router.put("/skills/{name}")
async def update_skill(request: Request, name: str, body: SkillUpdateRequest):
    from openakita.config import settings

    skill_dir = (settings.project_root / "skills" / name).resolve()
    if not skill_dir.is_relative_to((settings.project_root / "skills").resolve()):
        raise HTTPException(403, "Path traversal detected")
    meta_file = skill_dir / "metadata.json"
    if not skill_dir.exists():
        raise HTTPException(404, "Skill not found")
    meta = _read_json(meta_file) or {}
    if not meta.get("auto_generated"):
        raise HTTPException(403, "Only auto-generated skills can be modified")
    meta["enabled"] = body.enabled
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "name": name, "enabled": body.enabled}


@router.delete("/skills/{name}")
async def delete_skill(request: Request, name: str):
    from openakita.config import settings

    skill_dir = (settings.project_root / "skills" / name).resolve()
    if not skill_dir.is_relative_to((settings.project_root / "skills").resolve()):
        raise HTTPException(403, "Path traversal detected")
    if not skill_dir.exists():
        raise HTTPException(404, "Skill not found")
    meta = _read_json(skill_dir / "metadata.json") or {}
    if not meta.get("auto_generated"):
        raise HTTPException(403, "Only auto-generated skills can be deleted")
    shutil.rmtree(skill_dir)
    return {"status": "ok", "name": name}


# ── Patterns ──


@router.get("/patterns")
async def list_patterns(request: Request):
    path = _data_dir() / "patterns" / "effective_patterns.json"
    data = _read_json(path)
    if not isinstance(data, list):
        data = []
    for i, p in enumerate(data):
        p["_index"] = i
    return {"patterns": data, "total": len(data)}


class PatternUpdateRequest(BaseModel):
    enabled: bool


@router.put("/patterns/{idx}")
async def update_pattern(request: Request, idx: int, body: PatternUpdateRequest):
    path = _data_dir() / "patterns" / "effective_patterns.json"
    data = _read_json(path)
    if not isinstance(data, list) or idx < 0 or idx >= len(data):
        raise HTTPException(404, "Pattern not found")
    data[idx]["enabled"] = body.enabled
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "index": idx, "enabled": body.enabled}


# ── Prompts ──


@router.get("/prompts")
async def list_prompt_variants(request: Request, limit: int = 50):
    archive_dir = _data_dir() / "prompt_variants" / "archive"
    variants = []
    for f in _list_json_files(archive_dir)[:limit]:
        data = _read_json(f)
        if data:
            data["_file"] = f.name
            variants.append(data)
    return {"variants": variants, "total": len(variants)}


class PromptActivateRequest(BaseModel):
    section: str
    original: str
    proposed: str


@router.post("/prompts/{filename}/activate")
async def activate_prompt_variant(request: Request, filename: str, body: PromptActivateRequest):
    from openakita.config import settings

    allowed = {"identity/AGENT.md", "identity/POLICIES.yaml"}
    if body.section not in allowed:
        raise HTTPException(403, "Section not in allowed list")

    target = (settings.project_root / body.section).resolve()
    if not target.is_relative_to(settings.project_root.resolve()) or not target.exists():
        raise HTTPException(404, "Target file not found")

    content = target.read_text(encoding="utf-8")
    from openakita.evolution.experiment_loop import ExperimentLoop

    new_content, match_err = ExperimentLoop._fuzzy_match_and_replace(
        content, body.original, body.proposed
    )
    if new_content is None:
        raise HTTPException(400, match_err)
    target.write_text(new_content, encoding="utf-8")
    return {"status": "ok", "section": body.section}


# ── Approvals ──


def _get_approval_queue():
    from openakita.evolution.approval_queue import ApprovalQueue

    return ApprovalQueue()


@router.get("/approvals")
async def list_approvals(request: Request, status: str | None = None):
    queue = _get_approval_queue()
    all_items = queue.list_all()
    pc = sum(1 for i in all_items if i.get("status") == "pending")
    items = [i for i in all_items if i.get("status") == status] if status else all_items
    return {"approvals": items, "total": len(items), "pending_count": pc}


@router.get("/approvals/{req_id}")
async def get_approval(request: Request, req_id: str):
    queue = _get_approval_queue()
    data = queue.get(req_id)
    if not data:
        raise HTTPException(404, "Approval request not found")
    return data


class ApprovalDecision(BaseModel):
    action: str
    reason: str = ""


@router.post("/approvals/{req_id}")
async def resolve_approval(request: Request, req_id: str, body: ApprovalDecision):
    queue = _get_approval_queue()
    if body.action == "approve":
        success, msg = queue.approve_and_apply(req_id)
        if not success:
            raise HTTPException(400, msg)
        return {"status": "ok", "message": msg}
    elif body.action == "reject":
        if not queue.reject(req_id, body.reason):
            raise HTTPException(400, "Cannot reject: request not found or already resolved")
        return {"status": "rejected"}
    else:
        raise HTTPException(400, f"Invalid action: {body.action}")
