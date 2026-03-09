"""
组织编排 API 路由

CRUD + 模板 + 节点管理 + 生命周期 + 命令 + 记忆 + 事件
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from openakita.core.engine_bridge import to_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/orgs", tags=["组织编排"])

_VALID_DECISIONS = {"approve", "reject", "批准", "拒绝"}


def _safe_int(value: str | None, default: int) -> int:
    """Parse query param to int, returning *default* on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _get_manager(request: Request):
    mgr = getattr(request.app.state, "org_manager", None)
    if mgr is None:
        raise HTTPException(503, "OrgManager not initialized")
    return mgr


def _get_runtime(request: Request):
    rt = getattr(request.app.state, "org_runtime", None)
    if rt is None:
        raise HTTPException(503, "OrgRuntime not initialized")
    return rt


# ---- Organization CRUD ----

@router.get("")
async def list_orgs(request: Request, include_archived: bool = False):
    mgr = _get_manager(request)
    return mgr.list_orgs(include_archived=include_archived)


@router.post("", status_code=201)
async def create_org(request: Request):
    mgr = _get_manager(request)
    body = await request.json()
    org = mgr.create(body)
    return org.to_dict()


@router.get("/templates")
async def list_templates(request: Request):
    mgr = _get_manager(request)
    return mgr.list_templates()


@router.get("/templates/{template_id}")
async def get_template(request: Request, template_id: str):
    mgr = _get_manager(request)
    tpl = mgr.get_template(template_id)
    if tpl is None:
        raise HTTPException(404, f"Template not found: {template_id}")
    return tpl


@router.post("/from-template", status_code=201)
async def create_from_template(request: Request):
    mgr = _get_manager(request)
    body = await request.json()
    template_id = body.pop("template_id", None)
    if not template_id:
        raise HTTPException(400, "template_id is required")
    try:
        org = mgr.create_from_template(template_id, overrides=body)
    except FileNotFoundError:
        raise HTTPException(404, f"Template not found: {template_id}")
    return org.to_dict()


@router.get("/{org_id}")
async def get_org(request: Request, org_id: str):
    mgr = _get_manager(request)
    org = mgr.get(org_id)
    if org is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    return org.to_dict()


@router.put("/{org_id}")
async def update_org(request: Request, org_id: str):
    mgr = _get_manager(request)
    if mgr.get(org_id) is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    body = await request.json()
    try:
        org = mgr.update(org_id, body)
    except (ValueError, TypeError, KeyError) as e:
        raise HTTPException(400, f"Invalid org data: {e}")
    return org.to_dict()


@router.delete("/{org_id}")
async def delete_org(request: Request, org_id: str):
    mgr = _get_manager(request)
    if not mgr.delete(org_id):
        raise HTTPException(404, f"Organization not found: {org_id}")
    return {"ok": True}


@router.post("/{org_id}/duplicate", status_code=201)
async def duplicate_org(request: Request, org_id: str):
    mgr = _get_manager(request)
    if mgr.get(org_id) is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    new_name = body.get("name")
    org = mgr.duplicate(org_id, new_name=new_name)
    return org.to_dict()


@router.post("/{org_id}/archive")
async def archive_org(request: Request, org_id: str):
    mgr = _get_manager(request)
    if mgr.get(org_id) is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    org = mgr.archive(org_id)
    return org.to_dict()


@router.post("/{org_id}/unarchive")
async def unarchive_org(request: Request, org_id: str):
    mgr = _get_manager(request)
    if mgr.get(org_id) is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    org = mgr.unarchive(org_id)
    return org.to_dict()


@router.post("/{org_id}/save-as-template")
async def save_as_template(request: Request, org_id: str):
    mgr = _get_manager(request)
    if mgr.get(org_id) is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    tid = mgr.save_as_template(org_id, template_id=body.get("template_id"))
    return {"template_id": tid}


@router.post("/{org_id}/export")
async def export_org(request: Request, org_id: str):
    mgr = _get_manager(request)
    org = mgr.get(org_id)
    if org is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    import json as _json
    org_dir = mgr._org_dir(org_id)
    export_data: dict[str, Any] = {"organization": org.to_dict(), "files": {}}
    for sub in ("memory", "events", "logs", "reports", "policies"):
        sub_dir = org_dir / sub
        if sub_dir.is_dir():
            for f in sub_dir.rglob("*"):
                if f.is_file() and f.suffix in (".jsonl", ".json", ".md"):
                    rel = str(f.relative_to(org_dir)).replace("\\", "/")
                    try:
                        export_data["files"][rel] = f.read_text(encoding="utf-8")[:50000]
                    except Exception:
                        pass
    return export_data


# ---- Node Schedules ----

@router.get("/{org_id}/nodes/{node_id}/schedules")
async def list_node_schedules(request: Request, org_id: str, node_id: str):
    mgr = _get_manager(request)
    org = mgr.get(org_id)
    if org is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    if org.get_node(node_id) is None:
        raise HTTPException(404, f"Node not found: {node_id}")
    schedules = mgr.get_node_schedules(org_id, node_id)
    return [s.to_dict() for s in schedules]


@router.post("/{org_id}/nodes/{node_id}/schedules", status_code=201)
async def create_node_schedule(request: Request, org_id: str, node_id: str):
    mgr = _get_manager(request)
    org = mgr.get(org_id)
    if org is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    if org.get_node(node_id) is None:
        raise HTTPException(404, f"Node not found: {node_id}")
    body = await request.json()
    from openakita.orgs.models import NodeSchedule
    schedule = NodeSchedule.from_dict(body)
    mgr.add_node_schedule(org_id, node_id, schedule)
    return schedule.to_dict()


@router.put("/{org_id}/nodes/{node_id}/schedules/{schedule_id}")
async def update_node_schedule(
    request: Request, org_id: str, node_id: str, schedule_id: str
):
    mgr = _get_manager(request)
    body = await request.json()
    result = mgr.update_node_schedule(org_id, node_id, schedule_id, body)
    if result is None:
        raise HTTPException(404, f"Schedule not found: {schedule_id}")
    return result.to_dict()


@router.delete("/{org_id}/nodes/{node_id}/schedules/{schedule_id}")
async def delete_node_schedule(
    request: Request, org_id: str, node_id: str, schedule_id: str
):
    mgr = _get_manager(request)
    if not mgr.delete_node_schedule(org_id, node_id, schedule_id):
        raise HTTPException(404, f"Schedule not found: {schedule_id}")
    return {"ok": True}


# ---- Node Identity (read/write) ----

@router.get("/{org_id}/nodes/{node_id}/identity")
async def get_node_identity(request: Request, org_id: str, node_id: str):
    mgr = _get_manager(request)
    org = mgr.get(org_id)
    if org is None:
        raise HTTPException(404)
    if org.get_node(node_id) is None:
        raise HTTPException(404)
    node_dir = mgr._node_dir(org_id, node_id) / "identity"
    result: dict[str, str | None] = {}
    for fname in ("SOUL.md", "AGENT.md", "ROLE.md"):
        p = node_dir / fname
        result[fname] = p.read_text(encoding="utf-8") if p.is_file() else None
    return result


@router.put("/{org_id}/nodes/{node_id}/identity")
async def update_node_identity(request: Request, org_id: str, node_id: str):
    mgr = _get_manager(request)
    org = mgr.get(org_id)
    if org is None:
        raise HTTPException(404)
    if org.get_node(node_id) is None:
        raise HTTPException(404)
    body = await request.json()
    node_dir = mgr._node_dir(org_id, node_id) / "identity"
    node_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("SOUL.md", "AGENT.md", "ROLE.md"):
        if fname in body:
            p = node_dir / fname
            content = body[fname]
            if content is None or content == "":
                p.unlink(missing_ok=True)
            else:
                p.write_text(content, encoding="utf-8")
    return {"ok": True}


# ---- Node MCP Config ----

@router.get("/{org_id}/nodes/{node_id}/mcp")
async def get_node_mcp(request: Request, org_id: str, node_id: str):
    mgr = _get_manager(request)
    org = mgr.get(org_id)
    if org is None:
        raise HTTPException(404)
    if org.get_node(node_id) is None:
        raise HTTPException(404)
    import json
    p = mgr._node_dir(org_id, node_id) / "mcp_config.json"
    if not p.is_file():
        return {"mode": "inherit"}
    return json.loads(p.read_text(encoding="utf-8"))


@router.put("/{org_id}/nodes/{node_id}/mcp")
async def update_node_mcp(request: Request, org_id: str, node_id: str):
    mgr = _get_manager(request)
    org = mgr.get(org_id)
    if org is None:
        raise HTTPException(404)
    if org.get_node(node_id) is None:
        raise HTTPException(404)
    import json
    body = await request.json()
    p = mgr._node_dir(org_id, node_id) / "mcp_config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


# ---- Lifecycle ----

@router.post("/{org_id}/start")
async def start_org(request: Request, org_id: str):
    rt = _get_runtime(request)
    try:
        org = await to_engine(rt.start_org(org_id))
        return org.to_dict()
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{org_id}/stop")
async def stop_org(request: Request, org_id: str):
    rt = _get_runtime(request)
    try:
        org = await to_engine(rt.stop_org(org_id))
        return org.to_dict()
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{org_id}/pause")
async def pause_org(request: Request, org_id: str):
    rt = _get_runtime(request)
    try:
        org = await to_engine(rt.pause_org(org_id))
        return org.to_dict()
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{org_id}/resume")
async def resume_org(request: Request, org_id: str):
    rt = _get_runtime(request)
    try:
        org = await to_engine(rt.resume_org(org_id))
        return org.to_dict()
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{org_id}/reset")
async def reset_org(request: Request, org_id: str):
    rt = _get_runtime(request)
    try:
        org = await to_engine(rt.reset_org(org_id))
        return org.to_dict()
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---- User commands ----

@router.post("/{org_id}/command")
async def send_command(request: Request, org_id: str):
    rt = _get_runtime(request)
    body = await request.json()
    content = body.get("content", "")
    target_node = body.get("target_node_id")
    if not content:
        raise HTTPException(400, "content is required")
    try:
        result = await to_engine(rt.send_command(org_id, target_node, content))
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{org_id}/broadcast")
async def broadcast_to_org(request: Request, org_id: str):
    rt = _get_runtime(request)
    body = await request.json()
    content = body.get("content", "")
    if not content:
        raise HTTPException(400, "content is required")
    result = await to_engine(rt.handle_org_tool(
        "org_broadcast",
        {"content": content, "scope": "organization"},
        org_id, "user",
    ))
    return {"result": result}


# ---- Node management ----

@router.get("/{org_id}/nodes/{node_id}/status")
async def get_node_status(request: Request, org_id: str, node_id: str):
    rt = _get_runtime(request)
    org = rt.get_org(org_id)
    if not org:
        raise HTTPException(404, "Organization not found")
    node = org.get_node(node_id)
    if not node:
        raise HTTPException(404, f"Node not found: {node_id}")
    messenger = rt.get_messenger(org_id)
    pending = messenger.get_pending_count(node.id) if messenger else 0
    return {
        "id": node.id,
        "role_title": node.role_title,
        "status": node.status.value,
        "department": node.department,
        "pending_messages": pending,
        "frozen_by": node.frozen_by,
        "frozen_reason": node.frozen_reason,
        "frozen_at": node.frozen_at,
    }

@router.get("/{org_id}/nodes/{node_id}/thinking")
async def get_node_thinking(request: Request, org_id: str, node_id: str):
    """Get a node's recent thinking process: events, messages, and tool calls."""
    rt = _get_runtime(request)
    org = rt.get_org(org_id)
    if not org:
        raise HTTPException(404, "Organization not found")
    node = org.get_node(node_id)
    if not node:
        raise HTTPException(404, f"Node not found: {node_id}")

    limit = _safe_int(request.query_params.get("limit"), 30)
    es = rt.get_event_store(org_id)

    events = es.query(actor=node_id, limit=limit) if es else []

    org_dir = rt._manager._org_dir(org_id)
    comm_log = org_dir / "logs" / "communications.jsonl"
    messages: list[dict] = []
    if comm_log.is_file():
        import json as _json
        try:
            lines = comm_log.read_text(encoding="utf-8").strip().split("\n")
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    msg = _json.loads(line)
                except Exception:
                    continue
                if msg.get("from_node") == node_id or msg.get("to_node") == node_id:
                    messages.append(msg)
                    if len(messages) >= limit:
                        break
                    continue
        except Exception:
            pass

    timeline: list[dict] = []
    for evt in events:
        timeline.append({
            "type": "event",
            "timestamp": evt.get("timestamp", ""),
            "event_type": evt.get("event_type", ""),
            "data": evt.get("data", {}),
        })
    for msg in messages:
        timeline.append({
            "type": "message",
            "timestamp": msg.get("timestamp", msg.get("created_at", "")),
            "direction": "out" if msg.get("from_node") == node_id else "in",
            "peer": msg.get("to_node") if msg.get("from_node") == node_id else msg.get("from_node"),
            "msg_type": msg.get("msg_type", ""),
            "content": msg.get("content", "")[:500],
        })
    timeline.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    return {
        "node_id": node_id,
        "role_title": node.role_title,
        "status": node.status.value,
        "timeline": timeline[:limit],
    }


@router.get("/{org_id}/nodes/{node_id}/prompt-preview")
async def preview_node_prompt(request: Request, org_id: str, node_id: str):
    """Preview the assembled prompt for a node (without creating an agent)."""
    rt = _get_runtime(request)
    org = rt.get_org(org_id)
    if not org:
        raise HTTPException(404, "Organization not found")
    node = org.get_node(node_id)
    if not node:
        raise HTTPException(404, f"Node not found: {node_id}")

    identity = rt._get_identity(org_id)
    resolved = identity.resolve(node, org)

    bb = rt.get_blackboard(org_id)
    blackboard_summary = bb.get_org_summary() if bb else ""
    dept_summary = bb.get_dept_summary(node.department) if bb and node.department else ""
    node_summary = bb.get_node_summary(node.id) if bb else ""

    full_prompt = identity.build_org_context_prompt(
        node, org, resolved,
        blackboard_summary=blackboard_summary,
        dept_summary=dept_summary,
        node_summary=node_summary,
    )

    return {
        "node_id": node_id,
        "identity_level": resolved.level,
        "role_text": resolved.role or "(auto-generated)",
        "full_prompt": full_prompt,
        "char_count": len(full_prompt),
    }


@router.post("/{org_id}/nodes/{node_id}/freeze")
async def freeze_node(request: Request, org_id: str, node_id: str):
    rt = _get_runtime(request)
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    result = await to_engine(rt.handle_org_tool(
        "org_freeze_node",
        {"node_id": node_id, "reason": body.get("reason", "用户操作")},
        org_id, "user",
    ))
    return {"result": result}


@router.post("/{org_id}/nodes/{node_id}/unfreeze")
async def unfreeze_node(request: Request, org_id: str, node_id: str):
    rt = _get_runtime(request)
    result = await to_engine(rt.handle_org_tool(
        "org_unfreeze_node", {"node_id": node_id}, org_id, "user",
    ))
    return {"result": result}


@router.post("/{org_id}/nodes/{node_id}/offline")
async def set_node_offline(request: Request, org_id: str, node_id: str):
    rt = _get_runtime(request)
    org = rt.get_org(org_id)
    if not org:
        raise HTTPException(404, "Organization not found")
    node = org.get_node(node_id)
    if not node:
        raise HTTPException(404, f"Node not found: {node_id}")
    from openakita.orgs.models import NodeStatus
    node.status = NodeStatus.OFFLINE
    rt._save_org(org)
    rt.get_event_store(org_id).emit("node_deactivated", "user", {"node_id": node_id})
    return {"ok": True, "status": "offline"}


@router.post("/{org_id}/nodes/{node_id}/online")
async def set_node_online(request: Request, org_id: str, node_id: str):
    rt = _get_runtime(request)
    org = rt.get_org(org_id)
    if not org:
        raise HTTPException(404, "Organization not found")
    node = org.get_node(node_id)
    if not node:
        raise HTTPException(404, f"Node not found: {node_id}")
    from openakita.orgs.models import NodeStatus
    if node.status != NodeStatus.OFFLINE:
        raise HTTPException(400, f"Node is not offline (current: {node.status.value})")
    node.status = NodeStatus.IDLE
    rt._save_org(org)
    rt.get_event_store(org_id).emit("node_activated", "user", {"node_id": node_id})
    return {"ok": True, "status": "idle"}


# ---- Memory ----

@router.get("/{org_id}/memory")
async def query_memory(request: Request, org_id: str):
    rt = _get_runtime(request)
    bb = rt.get_blackboard(org_id)
    if not bb:
        mgr = _get_manager(request)
        org_dir = mgr._org_dir(org_id)
        from openakita.orgs.blackboard import OrgBlackboard
        bb = OrgBlackboard(org_dir, org_id)

    scope = request.query_params.get("scope")
    memory_type = request.query_params.get("type")
    tag = request.query_params.get("tag")
    limit = _safe_int(request.query_params.get("limit"), 50)

    from openakita.orgs.models import MemoryScope, MemoryType
    try:
        scope_enum = MemoryScope(scope) if scope else None
        type_enum = MemoryType(memory_type) if memory_type else None
    except ValueError:
        raise HTTPException(400, "Invalid scope or memory_type value")

    entries = bb.query(scope=scope_enum, memory_type=type_enum, tag=tag, limit=limit)
    return [e.to_dict() for e in entries]


@router.post("/{org_id}/memory", status_code=201)
async def add_memory(request: Request, org_id: str):
    rt = _get_runtime(request)
    bb = rt.get_blackboard(org_id)
    if not bb:
        mgr = _get_manager(request)
        from openakita.orgs.blackboard import OrgBlackboard
        bb = OrgBlackboard(mgr._org_dir(org_id), org_id)
    body = await request.json()
    from openakita.orgs.models import MemoryType, MemoryScope
    try:
        scope = MemoryScope(body.get("scope", "org"))
        mt = MemoryType(body.get("memory_type", "fact"))
    except ValueError as e:
        raise HTTPException(400, f"Invalid scope or memory_type: {e}")
    content = body.get("content", "")
    if not content:
        raise HTTPException(400, "content is required")
    if scope == MemoryScope.ORG:
        entry = bb.write_org(content, source_node="user", memory_type=mt,
                             tags=body.get("tags", []), importance=body.get("importance", 0.5))
    elif scope == MemoryScope.DEPARTMENT:
        dept = body.get("scope_owner", "")
        if not dept:
            raise HTTPException(400, "scope_owner (department) required for department scope")
        entry = bb.write_department(dept, content, "user", memory_type=mt,
                                    tags=body.get("tags", []), importance=body.get("importance", 0.5))
    else:
        node_id = body.get("scope_owner", "")
        if not node_id:
            raise HTTPException(400, "scope_owner (node_id) required for node scope")
        entry = bb.write_node(node_id, content, memory_type=mt,
                              tags=body.get("tags", []), importance=body.get("importance", 0.5))
    return entry.to_dict()


@router.delete("/{org_id}/memory/{memory_id}")
async def delete_memory(request: Request, org_id: str, memory_id: str):
    rt = _get_runtime(request)
    bb = rt.get_blackboard(org_id)
    if not bb:
        raise HTTPException(404, "Blackboard not available")
    ok = bb.delete_entry(memory_id)
    if not ok:
        raise HTTPException(404, f"Memory entry not found: {memory_id}")
    return {"ok": True}


# ---- Events ----

@router.get("/{org_id}/events")
async def query_events(request: Request, org_id: str):
    rt = _get_runtime(request)
    es = rt.get_event_store(org_id)
    event_type = request.query_params.get("event_type")
    actor = request.query_params.get("actor")
    since = request.query_params.get("since")
    until = request.query_params.get("until")
    limit = _safe_int(request.query_params.get("limit"), 100)
    events = es.query(
        event_type=event_type, actor=actor,
        since=since, until=until, limit=limit,
    )
    return events


# ---- Messages (communication log) ----

@router.get("/{org_id}/messages")
async def query_messages(request: Request, org_id: str):
    mgr = _get_manager(request)
    org_dir = mgr._org_dir(org_id)
    comm_log = org_dir / "logs" / "communications.jsonl"
    if not comm_log.is_file():
        return {"messages": [], "count": 0}
    import json as _json
    messages: list[dict] = []
    limit = _safe_int(request.query_params.get("limit"), 100)
    from_node = request.query_params.get("from_node")
    to_node = request.query_params.get("to_node")
    try:
        lines = comm_log.read_text(encoding="utf-8").strip().split("\n")
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                msg = _json.loads(line)
            except Exception:
                continue
            if from_node and msg.get("from_node") != from_node:
                continue
            if to_node and msg.get("to_node") != to_node:
                continue
            messages.append(msg)
            if len(messages) >= limit:
                break
    except Exception:
        pass
    return {"messages": messages, "count": len(messages)}


# ---- Policies ----

@router.get("/{org_id}/policies")
async def list_policies(request: Request, org_id: str):
    mgr = _get_manager(request)
    org_dir = mgr._org_dir(org_id)
    policies_dir = org_dir / "policies"
    if not policies_dir.exists():
        return []
    result = []
    for f in sorted(policies_dir.glob("*.md")):
        result.append({"filename": f.name, "size": f.stat().st_size})
    return result


@router.get("/{org_id}/policies/search")
async def search_policies(request: Request, org_id: str):
    rt = _get_runtime(request)
    policies = rt.get_policies(org_id)
    query = request.query_params.get("q", "")
    if not query:
        raise HTTPException(400, "Query parameter 'q' is required")
    return policies.search(query)


@router.get("/{org_id}/policies/{filename}")
async def read_policy(request: Request, org_id: str, filename: str):
    mgr = _get_manager(request)
    if ".." in filename:
        raise HTTPException(400, "Invalid filename")
    p = mgr._org_dir(org_id) / "policies" / filename
    if not p.is_file():
        raise HTTPException(404, f"Policy not found: {filename}")
    return {"filename": filename, "content": p.read_text(encoding="utf-8")}


@router.put("/{org_id}/policies/{filename}")
async def write_policy(request: Request, org_id: str, filename: str):
    mgr = _get_manager(request)
    if ".." in filename:
        raise HTTPException(400, "Invalid filename")
    body = await request.json()
    content = body.get("content", "")
    p = mgr._org_dir(org_id) / "policies" / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"ok": True}


@router.delete("/{org_id}/policies/{filename}")
async def delete_policy(request: Request, org_id: str, filename: str):
    mgr = _get_manager(request)
    if ".." in filename:
        raise HTTPException(400, "Invalid filename")
    p = mgr._org_dir(org_id) / "policies" / filename
    if not p.is_file():
        raise HTTPException(404)
    p.unlink()
    return {"ok": True}


# ---- Inbox ----

@router.get("/{org_id}/inbox")
async def list_inbox(request: Request, org_id: str):
    rt = _get_runtime(request)
    inbox = rt.get_inbox(org_id)
    unread_only = request.query_params.get("unread_only", "").lower() == "true"
    category = request.query_params.get("category")
    pending_only = request.query_params.get("pending_approval", "").lower() == "true"
    limit = _safe_int(request.query_params.get("limit"), 50)
    offset = _safe_int(request.query_params.get("offset"), 0)
    messages = inbox.list_messages(
        org_id,
        unread_only=unread_only,
        category=category,
        pending_approval_only=pending_only,
        limit=limit,
        offset=offset,
    )
    return {
        "messages": [m.to_dict() for m in messages],
        "unread_count": inbox.unread_count(org_id),
        "pending_approvals": inbox.pending_approval_count(org_id),
    }


@router.post("/{org_id}/inbox/{msg_id}/read")
async def mark_inbox_read(request: Request, org_id: str, msg_id: str):
    rt = _get_runtime(request)
    inbox = rt.get_inbox(org_id)
    ok = inbox.mark_read(org_id, msg_id)
    if not ok:
        raise HTTPException(404, "Message not found or already read")
    return {"ok": True}


@router.post("/{org_id}/inbox/read-all")
async def mark_all_inbox_read(request: Request, org_id: str):
    rt = _get_runtime(request)
    inbox = rt.get_inbox(org_id)
    count = inbox.mark_all_read(org_id)
    return {"marked": count}


@router.post("/{org_id}/inbox/{msg_id}/resolve")
async def resolve_inbox_approval(request: Request, org_id: str, msg_id: str):
    rt = _get_runtime(request)
    inbox = rt.get_inbox(org_id)
    body = await request.json()
    decision = body.get("decision", "").strip().lower()
    if not decision:
        raise HTTPException(400, "decision is required")
    if decision not in _VALID_DECISIONS:
        raise HTTPException(400, f"Invalid decision. Must be one of: {', '.join(sorted(_VALID_DECISIONS))}")
    msg = inbox.resolve_approval(org_id, msg_id, decision, by="user")
    if not msg:
        raise HTTPException(404, "Message not found or not an approval")
    return msg.to_dict()


# ---- Scaling ----

@router.get("/{org_id}/scaling/requests")
async def list_scaling_requests(request: Request, org_id: str):
    rt = _get_runtime(request)
    scaler = rt.get_scaler()
    reqs = scaler.get_pending_requests(org_id)
    return [
        {
            "id": r.id,
            "type": r.request_type,
            "requester": r.requester_node_id,
            "source_node_id": r.source_node_id,
            "role_title": r.role_title,
            "reason": r.reason,
            "status": r.status,
            "created_at": r.created_at,
        }
        for r in reqs
    ]


@router.post("/{org_id}/scaling/{request_id}/approve")
async def approve_scaling(request: Request, org_id: str, request_id: str):
    rt = _get_runtime(request)
    scaler = rt.get_scaler()
    try:
        req = scaler.approve_request(org_id, request_id, approved_by="user")
        return {
            "id": req.id,
            "status": req.status,
            "result_node_id": req.result_node_id,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{org_id}/scaling/{request_id}/reject")
async def reject_scaling(request: Request, org_id: str, request_id: str):
    rt = _get_runtime(request)
    scaler = rt.get_scaler()
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    try:
        req = scaler.reject_request(
            org_id, request_id,
            rejected_by="user", reason=body.get("reason", ""),
        )
        return {"id": req.id, "status": req.status}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{org_id}/scale/clone")
async def scale_clone(request: Request, org_id: str):
    rt = _get_runtime(request)
    scaler = rt.get_scaler()
    body = await request.json()
    source_node_id = body.get("source_node_id")
    if not source_node_id:
        raise HTTPException(400, "source_node_id is required")
    try:
        req = scaler.request_clone(
            org_id=org_id,
            requester="user",
            source_node_id=source_node_id,
            reason=body.get("reason", "用户手动克隆"),
            ephemeral=body.get("ephemeral", True),
        )
        return {
            "id": req.id,
            "status": req.status,
            "result_node_id": req.result_node_id,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{org_id}/scale/recruit")
async def scale_recruit(request: Request, org_id: str):
    rt = _get_runtime(request)
    scaler = rt.get_scaler()
    body = await request.json()
    role_title = body.get("role_title")
    parent_node_id = body.get("parent_node_id")
    if not role_title or not parent_node_id:
        raise HTTPException(400, "role_title and parent_node_id are required")
    try:
        req = scaler.request_recruit(
            org_id=org_id,
            requester="user",
            role_title=role_title,
            role_goal=body.get("role_goal", ""),
            department=body.get("department", ""),
            parent_node_id=parent_node_id,
            reason=body.get("reason", "用户手动招募"),
        )
        return {"id": req.id, "status": req.status}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{org_id}/nodes/{node_id}/dismiss")
async def dismiss_node(request: Request, org_id: str, node_id: str):
    rt = _get_runtime(request)
    scaler = rt.get_scaler()
    ok = scaler.dismiss_node(org_id, node_id, by="user")
    if not ok:
        raise HTTPException(400, "Cannot dismiss this node (non-ephemeral or not found)")
    return {"ok": True}


# ---- SSE Status Stream ----

@router.get("/{org_id}/status")
async def org_status_stream(request: Request, org_id: str):
    """SSE stream for real-time organization status updates."""
    from fastapi.responses import StreamingResponse
    import asyncio as _asyncio
    import json as _json

    rt = _get_runtime(request)
    org = rt.get_org(org_id)
    if not org:
        raise HTTPException(404, "Organization not found")

    async def _event_generator():
        yield f"data: {_json.dumps({'type': 'connected', 'org_id': org_id})}\n\n"

        inbox = rt.get_inbox(org_id)
        q = inbox.subscribe(org_id)
        try:
            while True:
                try:
                    msg = await _asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {_json.dumps({'type': 'inbox', 'message': msg.to_dict()}, ensure_ascii=False)}\n\n"
                except _asyncio.TimeoutError:
                    current = rt.get_org(org_id)
                    if not current:
                        break
                    node_states = {n.id: n.status.value for n in current.nodes}
                    yield f"data: {_json.dumps({'type': 'heartbeat', 'status': current.status.value, 'nodes': node_states})}\n\n"
        except _asyncio.CancelledError:
            pass
        finally:
            inbox.unsubscribe(org_id, q)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- Heartbeat / Standup ----

@router.post("/{org_id}/heartbeat/trigger")
async def trigger_heartbeat(request: Request, org_id: str):
    rt = _get_runtime(request)
    hb = rt.get_heartbeat()
    result = await hb.trigger_heartbeat(org_id)
    return result


@router.post("/{org_id}/standup/trigger")
async def trigger_standup(request: Request, org_id: str):
    rt = _get_runtime(request)
    hb = rt.get_heartbeat()
    result = await hb.trigger_standup(org_id)
    return result


# ---- Schedules trigger ----

@router.post("/{org_id}/nodes/{node_id}/schedules/{schedule_id}/trigger")
async def trigger_schedule(
    request: Request, org_id: str, node_id: str, schedule_id: str
):
    rt = _get_runtime(request)
    scheduler = rt.get_scheduler()
    result = await scheduler.trigger_once(org_id, node_id, schedule_id)
    return result


# ---- Reports ----

@router.get("/{org_id}/reports/summary")
async def get_report_summary(request: Request, org_id: str):
    rt = _get_runtime(request)
    es = rt.get_event_store(org_id)
    days = _safe_int(request.query_params.get("days"), 7)
    return es.generate_summary_report(days=days)


@router.post("/{org_id}/reports/generate")
async def generate_report(request: Request, org_id: str):
    rt = _get_runtime(request)
    es = rt.get_event_store(org_id)
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    days = body.get("days", 7)
    report_path = es.generate_report_markdown(days=days)
    return {"path": str(report_path), "ok": True}


@router.get("/{org_id}/audit-log")
async def get_audit_log(request: Request, org_id: str):
    rt = _get_runtime(request)
    es = rt.get_event_store(org_id)
    days = _safe_int(request.query_params.get("days"), 7)
    return es.get_audit_log(days=days)


# ---- Reports list ----

@router.get("/{org_id}/reports")
async def list_reports(request: Request, org_id: str):
    mgr = _get_manager(request)
    reports_dir = mgr._org_dir(org_id) / "reports"
    if not reports_dir.is_dir():
        return []
    result = []
    for f in sorted(reports_dir.glob("*.md"), reverse=True):
        result.append({
            "filename": f.name,
            "size": f.stat().st_size,
            "modified": f.stat().st_mtime,
        })
    return result


# ---- IM Notification Reply ----

@router.post("/{org_id}/im-reply")
async def handle_im_reply(request: Request, org_id: str):
    rt = _get_runtime(request)
    notifier = rt.get_notifier()
    body = await request.json()
    text = body.get("text", "")
    sender = body.get("sender", "user")
    if not text:
        raise HTTPException(400, "text is required")
    result = await notifier.handle_im_reply(org_id, text, sender=sender)
    return result


# ---- Event Replay (for log playback) ----

@router.get("/{org_id}/events/replay")
async def replay_events(request: Request, org_id: str):
    """Get events for timeline replay/playback visualization."""
    rt = _get_runtime(request)
    es = rt.get_event_store(org_id)
    since = request.query_params.get("since")
    until = request.query_params.get("until")
    node_id = request.query_params.get("node_id")
    limit = _safe_int(request.query_params.get("limit"), 200)

    events = es.query(
        actor=node_id,
        since=since,
        until=until,
        limit=limit,
    )
    events.sort(key=lambda e: e.get("timestamp", ""))

    timeline: list[dict] = []
    for evt in events:
        timeline.append({
            "t": evt.get("timestamp"),
            "type": evt.get("event_type"),
            "actor": evt.get("actor"),
            "data": evt.get("data", {}),
        })

    return {"events": timeline, "count": len(timeline)}


# ---- Organization stats ----

@router.get("/{org_id}/stats")
async def get_org_stats(request: Request, org_id: str):
    """Get real-time organization statistics with per-node runtime data."""
    import time as _time

    rt = _get_runtime(request)
    org = rt.get_org(org_id)
    if not org:
        raise HTTPException(404, "Organization not found")

    messenger = rt.get_messenger(org_id)
    inbox = rt.get_inbox(org_id)
    scaler = rt.get_scaler()

    node_stats: dict[str, int] = {}
    for n in org.nodes:
        s = n.status.value
        node_stats[s] = node_stats.get(s, 0) + 1

    pending_messages = 0
    if messenger:
        for n in org.nodes:
            pending_messages += messenger.get_pending_count(n.id)

    now_mono = _time.monotonic()
    per_node: list[dict] = []
    anomalies: list[dict] = []
    agent_cache = getattr(rt, "_agent_cache", None) or {}
    for n in org.nodes:
        cache_key = f"{org_id}:{n.id}"
        cached = agent_cache.get(cache_key) if isinstance(agent_cache, dict) else None
        idle_secs = None
        if cached:
            try:
                last = cached.last_used
                if isinstance(last, (int, float)) and last > 0:
                    idle_secs = now_mono - last
            except Exception:
                pass
        node_pending = messenger.get_pending_count(n.id) if messenger else 0
        entry = {
            "id": n.id,
            "role_title": n.role_title,
            "department": n.department,
            "status": n.status.value,
            "pending_messages": node_pending,
            "idle_seconds": round(idle_secs) if idle_secs is not None else None,
            "current_task": getattr(n, "_current_task_desc", None),
            "is_clone": n.is_clone,
            "frozen": n.frozen_by is not None,
        }
        per_node.append(entry)

        if n.status.value == "error":
            anomalies.append({"node_id": n.id, "role_title": n.role_title, "type": "error",
                              "message": "节点处于错误状态"})
        elif n.status.value == "busy" and idle_secs is not None and idle_secs > 600:
            anomalies.append({"node_id": n.id, "role_title": n.role_title, "type": "stuck",
                              "message": f"节点标记为忙碌但已 {round(idle_secs / 60)} 分钟无活动"})
        elif n.status.value == "idle" and idle_secs is not None and idle_secs > 300 and not n.is_clone:
            anomalies.append({"node_id": n.id, "role_title": n.role_title, "type": "long_idle",
                              "message": f"空闲超过 {round(idle_secs / 60)} 分钟"})
        if node_pending > 5:
            anomalies.append({"node_id": n.id, "role_title": n.role_title, "type": "backlog",
                              "message": f"待处理消息积压 {node_pending} 条"})

    bb = rt.get_blackboard(org_id)
    recent_bb: list[dict] = []
    if bb:
        try:
            entries = bb.read_org(limit=5)
            for e in entries:
                recent_bb.append({
                    "content": (e.content[:120] + "…") if len(e.content) > 120 else e.content,
                    "source_node": e.source_node,
                    "memory_type": e.memory_type.value if hasattr(e.memory_type, "value") else str(e.memory_type),
                    "timestamp": e.created_at,
                    "tags": e.tags[:3] if e.tags else [],
                })
        except Exception:
            pass

    uptime_s = None
    if org.created_at:
        try:
            from datetime import datetime, timezone
            start = datetime.fromisoformat(org.created_at.replace("Z", "+00:00"))
            uptime_s = round((datetime.now(timezone.utc) - start).total_seconds())
        except Exception:
            pass

    health = "healthy"
    if any(a["type"] == "error" for a in anomalies):
        health = "critical"
    elif any(a["type"] == "stuck" for a in anomalies):
        health = "warning"
    elif len(anomalies) > 0:
        health = "attention"

    return {
        "org_id": org.id,
        "name": org.name,
        "status": org.status.value,
        "health": health,
        "uptime_s": uptime_s,
        "node_count": len(org.nodes),
        "edge_count": len(org.edges),
        "node_stats": node_stats,
        "departments": org.get_departments(),
        "total_tasks_completed": org.total_tasks_completed,
        "total_messages_exchanged": org.total_messages_exchanged,
        "pending_messages": pending_messages,
        "unread_inbox": inbox.unread_count(org_id) if inbox else 0,
        "pending_approvals": inbox.pending_approval_count(org_id) if inbox else 0,
        "pending_scaling_requests": len(scaler.get_pending_requests(org_id)),
        "per_node": per_node,
        "anomalies": anomalies,
        "recent_blackboard": recent_bb,
    }


# =====================================================================
# Cross-organization inbox (mounted at /api/org-inbox)
# =====================================================================

inbox_router = APIRouter(prefix="/api/org-inbox", tags=["组织消息中心"])


@inbox_router.get("")
async def global_inbox(request: Request):
    """Get inbox messages from all active organizations."""
    rt = _get_runtime(request)
    mgr = _get_manager(request)
    limit = _safe_int(request.query_params.get("limit"), 50)
    offset = _safe_int(request.query_params.get("offset"), 0)
    priority = request.query_params.get("priority")
    org_filter = request.query_params.get("org_id")

    all_messages: list[dict] = []
    for info in mgr.list_orgs(include_archived=False):
        oid = info["id"]
        if org_filter and oid != org_filter:
            continue
        inbox = rt.get_inbox(oid)
        msgs = inbox.list_messages(oid, limit=200)
        for m in msgs:
            d = m.to_dict()
            if priority and d.get("priority") != priority:
                continue
            all_messages.append(d)

    all_messages.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    total = len(all_messages)
    page = all_messages[offset: offset + limit]
    return {"messages": page, "total": total}


@inbox_router.get("/unread-count")
async def global_unread_count(request: Request):
    """Get unread counts for all organizations grouped by priority."""
    rt = _get_runtime(request)
    mgr = _get_manager(request)
    counts: dict[str, int] = {}
    total_unread = 0
    for info in mgr.list_orgs(include_archived=False):
        oid = info["id"]
        inbox = rt.get_inbox(oid)
        c = inbox.unread_count(oid)
        if c > 0:
            counts[oid] = c
            total_unread += c
    return {"total_unread": total_unread, "by_org": counts}


@inbox_router.post("/{msg_id}/read")
async def global_inbox_mark_read(request: Request, msg_id: str):
    """Mark a message read (searches across orgs)."""
    rt = _get_runtime(request)
    mgr = _get_manager(request)
    for info in mgr.list_orgs(include_archived=False):
        oid = info["id"]
        inbox = rt.get_inbox(oid)
        if inbox.mark_read(oid, msg_id):
            return {"ok": True}
    raise HTTPException(404, "Message not found")


@inbox_router.post("/read-all")
async def global_inbox_read_all(request: Request):
    rt = _get_runtime(request)
    mgr = _get_manager(request)
    total = 0
    for info in mgr.list_orgs(include_archived=False):
        oid = info["id"]
        inbox = rt.get_inbox(oid)
        total += inbox.mark_all_read(oid)
    return {"marked": total}


@inbox_router.post("/{msg_id}/act")
async def global_inbox_act(request: Request, msg_id: str):
    """Execute action on an inbox message (approve/reject)."""
    rt = _get_runtime(request)
    mgr = _get_manager(request)
    body = await request.json()
    decision = body.get("decision", "").strip().lower()
    if not decision:
        raise HTTPException(400, "decision is required")
    if decision not in _VALID_DECISIONS:
        raise HTTPException(400, f"Invalid decision. Must be one of: {', '.join(sorted(_VALID_DECISIONS))}")
    for info in mgr.list_orgs(include_archived=False):
        oid = info["id"]
        inbox = rt.get_inbox(oid)
        msg = inbox.resolve_approval(oid, msg_id, decision, by="user")
        if msg:
            return msg.to_dict()
    raise HTTPException(404, "Message not found or not an approval")
